"""Script à lancer une seule fois pour générer token.json.

Le client OAuth ne supporte plus le flow "OOB" (copier-coller de code), donc
on utilise un petit serveur local le temps du consentement. Sur un VPS sans
navigateur, ouvre d'abord un tunnel SSH depuis ton poste :

    ssh -L 8080:localhost:8080 ubuntu@<ip-vps>

puis lance ce script dans la session SSH tunnelée, et ouvre l'URL affichée
dans le navigateur de ton poste. Une fois le consentement donné, le
navigateur redirige vers localhost:8080, qui remonte par le tunnel jusqu'à
ce script.
"""
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CLIENT_SECRET_FILE = "Client Secret Le Savais-tu.json"
TOKEN_FILE = "token.json"


def main() -> None:
    flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
    creds = flow.run_local_server(port=8080, open_browser=False)
    Path(TOKEN_FILE).write_text(creds.to_json(), encoding="utf-8")
    print(f"Token sauvegardé dans {TOKEN_FILE}")


if __name__ == "__main__":
    main()
