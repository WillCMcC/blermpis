import requests
import re
import json
import time

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def fetch_craigslist_data():
    base_url = "https://portland.craigslist.org/search/clk/syp"
    params = {
        "searchNearby": 2,
        "nearbyArea": "portland",
    }
    
    all_pages_content = []
    page = 0
    last_response = None
    while True:
        try:
            # Add the start index parameter
            params['s'] = page * 120  # Craigslist shows 120 items per page
            # Add delay between requests to be respectful
            if page > 0:
                time.sleep(1)
            
            print(f"Fetching page {page + 1}...")
            response = requests.get(base_url, params=params, timeout=10)
            if(last_response):
                print(str(response.content)[-100] == str(last_response.content)[-100])
                if response.content == last_response.content:
                    print(f"Duplicate page content detected. Ending search.")
                    break
            
            last_response = response
            
            # Simple check for "no results" text in the response
            if "no results" in response.text.lower() or "nothing found" in response.text.lower():
                print("No more results found.")
                break

            if response.status_code == 200:
                content = response.content
                all_pages_content.append(content)
               
                page += 1
            else:
                print(f"Failed to retrieve page {page + 1}. Status code: {response.status_code}")
                break
                
        except requests.exceptions.RequestException as e:
            print(f"Request error on page {page + 1}: {e}")
            break
        except Exception as e:
            print(f"General error on page {page + 1}: {e}")
            break
    
    return all_pages_content
fetch_craigslist_data()