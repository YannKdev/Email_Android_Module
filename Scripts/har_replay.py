import json
import requests

def replay_har(file_path):
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            har_data = json.load(f)
        
        entries = har_data.get('log', {}).get('entries', [])
        print(f"[*] {len(entries)} requête(s) trouvée(s) dans le fichier HAR.\n")

        for i, entry in enumerate(entries):
            request_data = entry.get('request', {})
            url = request_data.get('url')
            method = request_data.get('method')
            
            # 1. Préparation des Headers
            headers = {h['name']: h['value'] for h in request_data.get('headers', [])}
            
            # Supprimer l'en-tête Content-Length pour laisser requests le recalculer
            headers.pop('content-length', None)
            headers.pop('Content-Length', None)

            # 2. Préparation du Body (PostData)
            body = None
            if 'postData' in request_data:
                body = request_data['postData'].get('text')

            print(f"[REPLAY {i+1}] {method} -> {url}")

            # 3. Envoi de la requête
            # 3. Envoi de la requête
            try:
                response = requests.request(
                    method=method,
                    url=url,
                    headers=headers,
                    data=body,
                    timeout=10
                )
                
                print(f"Status: {response.status_code}")
                
                # Tentative d'affichage JSON formaté pour la lisibilité
                try:
                    json_response = response.json()
                    print("Response (JSON):")
                    print(json.dumps(json_response, indent=4, ensure_ascii=False))
                except:
                    # Si ce n'est pas du JSON, on affiche le texte brut complet
                    print("Response (Raw):")
                    print(response.text)
                
                print("-" * 50)
                
            except Exception as e:
                print(f"[!] Erreur lors de l'envoi : {e}")

    except FileNotFoundError:
        print("Erreur : Le fichier spécifié est introuvable.")
    except json.JSONDecodeError:
        print("Erreur : Le fichier n'est pas un JSON/HAR valide.")

if __name__ == "__main__":
    # Remplacez 'votre_fichier.har' par le nom de votre fichier
    replay_har('har_files/result.har')