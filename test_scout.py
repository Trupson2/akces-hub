import json
from app import app
from modules.database import init_db
from modules.winning_scout import scout_by_phrase, _log
from modules.allegro_api import allegro_request, is_authenticated

def debug_scout():
    with app.app_context():
        # Just manually fetching the raw response to debug filters
        resp, err = allegro_request("GET", "/offers/listing", params={
            "phrase": "wędkarstwo",
            "sort": "-popularity",
            "limit": 10,
            "searchMode": "REGULAR"
        })
        
        if err or not resp:
            print("ERROR FETCHING ALLEGRO:", err)
            return
            
        items = resp.get('items', {})
        offers = items.get('promoted', []) + items.get('regular', [])
        print(f"Got {len(offers)} offers from Allegro API.")
        
        for off in offers:
            name = off.get('name', '')
            selling_mode = off.get('sellingMode', {})
            sold = selling_mode.get('popularity')
            if sold is None:
                sold = off.get('stock', {}).get('sold', 151)
            
            sell_price = float(selling_mode.get('price', {}).get('amount', 0))
            print(f"[{name}] - sold: {sold}, price: {sell_price} PLN")

if __name__ == '__main__':
    debug_scout()
