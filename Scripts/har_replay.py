import json
import requests

def replay_har(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            har_data = json.load(f)
        
        entries = har_data.get('log', {}).get('entries', [])
        print(f"[*] {len(entries)} request(s) found in the HAR file.\n")

        for i, entry in enumerate(entries):
            request_data = entry.get('request', {})
            url = request_data.get('url')
            method = request_data.get('method')
            
            # 1. Prepare Headers
            headers = {h['name']: h['value'] for h in request_data.get('headers', [])}
            
            # Remove the Content-Length header to let requests recalculate it
            headers.pop('content-length', None)
            headers.pop('Content-Length', None)

            # 2. Prepare the Body (PostData)
            body = None
            if 'postData' in request_data:
                body = request_data['postData'].get('text')

            print(f"[REPLAY {i+1}] {method} -> {url}")

            # 3. Send the request
            # 3. Send the request
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body,
                    timeout=10
                )
                
                print(f"Status: {response.status_code}")
                
                # Attempt to display formatted JSON for readability
                try:
                    json_response = response.json()
                    print("Response (JSON):")
                    print(json.dumps(json_response, indent=4, ensure_ascii=False))
                except:
                    # If it is not JSON, display the full raw text
                    print("Response (Raw):")
                    print(response.text)
                
                print("-" * 50)
                
            except Exception as e:
                print(f"[!] Error during sending: {e}")

    except FileNotFoundError:
        print("Error: The specified file was not found.")
    except json.JSONDecodeError:
        print("Error: The file is not a valid JSON/HAR.")

if __name__ == "__main__":
    # Replace 'votre_fichier.har' with your file name
    replay_har('har_files/result.har')