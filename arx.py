import requests
import datetime
import json

station_id = "28B080"
data_types = ["DSG", "STG", "WTM", "ATM"]
current_year = datetime.datetime.now().year
start_year = current_year - 1

all_data = {}

for data_type in data_types:
    all_data[data_type] = {}
    # Fetch current data
    current_url = f"https://apps.ecology.wa.gov/ContinuousFlowAndWQ/StationData/Prod/{station_id}/{station_id}_{data_type}_FM.TXT"
    try:
        print("current_url")
        print(current_url)
        response = requests.get(current_url, timeout=10)
        response.raise_for_status()
        all_data[data_type]["current"] = response.text
    except requests.exceptions.RequestException as e:
        print(f"Error fetching current {data_type} data: {e}")
        all_data[data_type]["current"] = f"Error: {e}"

    # Fetch historical data for the last 10 years
    all_data[data_type]["historical"] = {}
    for year in range(start_year, current_year):
        historical_url = f"https://apps.ecology.wa.gov/ContinuousFlowAndWQ/StationData/Prod/{station_id}/{station_id}_{year}_{data_type}_FM.TXT"
        try:
            print("historical_url")
            print(historical_url)
            response = requests.get(historical_url, timeout=10)
            response.raise_for_status()
            all_data[data_type]["historical"][year] = response.text
        except requests.exceptions.RequestException as e:
            print(f"Error fetching historical {data_type} {year} data: {e}")
            all_data[data_type]["historical"][year] = f"Error: {e}"

# Print all data to standard output for gemini to use. Using json for proper formatting/parsing.
print(json.dumps(all_data))