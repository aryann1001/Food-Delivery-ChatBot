from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
import db_helper
import generic_helper
import logging

app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

inprogress_orders = {}

@app.post("/")
async def handle_request(request: Request):
    try:
        body = await request.body()
        logger.info(f"Raw request body: {body.decode('utf-8')}")

        if not body:
            raise HTTPException(status_code=400, detail="Empty request body")

        payload = await request.json()
        logger.info(f"Received payload: {payload}")

        intent = payload['queryResult']['intent']['displayName']
        parameters = payload['queryResult']['parameters']
        output_contexts = payload['queryResult']['outputContexts']

        session_id = generic_helper.extract_session_id(output_contexts[0]['name'])

        # Correcting the intent names to match exactly with Dialogflow intents
        intent_handler_dict = {
            'order.add- context: ongoing-order': add_to_order,
            'order.remove- context: ongoing-order': remove_from_order,
            'order.complete-context: ongoing-order': complete_order,
            'track.order-context: ongoing-tracking': track_order
        }

        if intent not in intent_handler_dict:
            logger.error(f"Intent '{intent}' not found in intent_handler_dict")
            raise HTTPException(status_code=400, detail=f"Intent '{intent}' not found")

        return intent_handler_dict[intent](parameters, session_id)

    except KeyError as e:
        logger.error(f"Missing key in request data: {e}")
        raise HTTPException(status_code=400, detail=f"Missing key in request data: {e}")
    except ValueError as e:
        logger.error(f"Error parsing JSON payload: {e}")
        raise HTTPException(status_code=400, detail="Invalid JSON payload")
    except HTTPException as e:
        logger.error(f"HTTPException: {e.detail}")
        raise e
    except Exception as e:
        logger.error(f"An unexpected error occurred: {e}")
        raise HTTPException(status_code=500, detail="An internal error occurred")

def add_to_order(parameters: dict, session_id: str):
    try:
        food_items = parameters["food-item"]
        quantities = parameters["number"]
        if len(food_items) != len(quantities):
            fulfillment_text = "Sorry I didn't understand. Can you please specify food items and quantities clearly?"
        else:
            new_food_dict = dict(zip(food_items, quantities))
            fulfillment_text = f"Received {food_items} and {quantities} in the backend"
            if session_id in inprogress_orders:
                current_food_dict = inprogress_orders[session_id]
                current_food_dict.update(new_food_dict)
                inprogress_orders[session_id] = current_food_dict
            else:
                inprogress_orders[session_id] = new_food_dict
            order_str = generic_helper.get_str_from_food_dict(inprogress_orders[session_id])
            fulfillment_text = f"So far you have: {order_str}. Do you need anything else?"

        return JSONResponse(content={"fulfillmentText": fulfillment_text})
    except Exception as e:
        logger.error(f"Error in add_to_order: {e}")
        raise HTTPException(status_code=500, detail="An error occurred in add_to_order")

def save_to_db(order: dict):
    try:
        next_order_id = db_helper.get_next_order_id()

        # Insert individual items along with quantity in orders table
        for food_item, quantity in order.items():
            rcode = db_helper.insert_order_item(food_item, quantity, next_order_id)
            if rcode == -1:
                return -1

        # Now insert order tracking status
        db_helper.insert_order_tracking(next_order_id, "in progress")

        return next_order_id
    except Exception as e:
        logger.error(f"Error in save_to_db: {e}")
        return -1

def remove_from_order(parameters: dict, session_id: str):
    if session_id not in inprogress_orders:
        return JSONResponse(content={
            "fulfillmentText": "I'm having a trouble finding your order. Sorry! Can you place a new order please?"
        })

    food_items = parameters["food-item"]
    current_order = inprogress_orders[session_id]

    removed_items = []
    no_such_items = []

    for item in food_items:
        if item not in current_order:
            no_such_items.append(item)
        else:
            removed_items.append(item)
            del current_order[item]

    if len(removed_items) > 0:
        fulfillment_text = f'Removed {",".join(removed_items)} from your order!'

    if len(no_such_items) > 0:
        fulfillment_text = f' Your current order does not have {",".join(no_such_items)}'

    if len(current_order.keys()) == 0:
        fulfillment_text += " Your order is empty!"
    else:
        order_str = generic_helper.get_str_from_food_dict(current_order)
        fulfillment_text += f" Here is what is left in your order: {order_str}"

    return JSONResponse(content={
        "fulfillmentText": fulfillment_text
    })


def complete_order(parameters: dict, session_id: str):
    try:
        if session_id not in inprogress_orders:
            fulfillment_text = "I'm having trouble finding your order. Sorry! Can you place a new order please?"
        else:
            order = inprogress_orders[session_id]
            order_id = save_to_db(order)
            if order_id == -1:
                fulfillment_text = "Sorry, I couldn't process your order due to a backend error. Please place a new order again"
            else:
                order_total = db_helper.get_total_order_price(order_id)
                fulfillment_text = f"Awesome. We have placed your order. Here is your order id # {order_id}. Your order total is {order_total} which you can pay at the time of delivery!"

            del inprogress_orders[session_id]

        return JSONResponse(content={"fulfillmentText": fulfillment_text})
    except Exception as e:
        logger.error(f"Error in complete_order: {e}")
        raise HTTPException(status_code=500, detail="An error occurred in complete_order")

def track_order(parameters: dict, session_id: str):
    try:
        order_id = int(parameters['number'])  # Ensure 'number' key is used
        order_status = db_helper.get_order_status(order_id)
        if order_status:
            fulfillment_text = f"The order status for order id: {order_id} is: {order_status}"
        else:
            fulfillment_text = f"No order found with order id: {order_id}"

        return JSONResponse(content={"fulfillmentText": fulfillment_text})
    except KeyError:
        logger.error("Missing 'number' in request data")
        raise HTTPException(status_code=400, detail="Missing 'number' in request data")
    except ValueError:
        logger.error("'number' must be an integer")
        raise HTTPException(status_code=400, detail="'number' must be an integer")
    except Exception as e:
        logger.error(f"Error in track_order: {e}")
        raise HTTPException(status_code=500, detail="An error occurred in track_order")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, log_level="info", reload=True)
