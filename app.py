import os
import json
import threading
# import ngrok
from flask import Flask, request, jsonify, render_template
from flask import jsonify 
from groq import Groq
from twilio.twiml.messaging_response import MessagingResponse
from dotenv import load_dotenv

# Carica variabili d'ambiente (.env)
load_dotenv()

app = Flask(__name__)
client = Groq(api_key=os.environ.get("GROQ_API_KEY"))

cronologia_chat = {}

# --- FUNZIONE PER SALVARE LA PRENOTAZIONE SU FILE JSON ---
def salva_prenotazione(numero_telefono, nome_cognome, motivo, data_ora):
    file_path = "prenotazioni.json"
    prenotazioni = []
    
    if os.path.exists(file_path):
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                prenotazioni = json.load(f)
        except Exception:
            prenotazioni = []
            
    nuova_prenotazione = {
        "telefono": numero_telefono,
        "nome_cognome": nome_cognome,
        "motivo": motivo,
        "data_ora": data_ora,
        "stato": "In attesa di conferma"
    }
    
    prenotazioni.append(nuova_prenotazione)
    
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(prenotazioni, f, ensure_ascii=False, indent=2)
        
    print(f"\n🎉 PRENOTAZIONE REGISTRATA: {nome_cognome} ({data_ora})\n")
    return f"Prenotazione registrata con successo nel sistema per {nome_cognome}."

# --- TOOL PER GROQ (FUNCTION CALLING) ---
tools = [
    {
        "type": "function",
        "function": {
            "name": "salva_prenotazione",
            "description": "Registra la richiesta di appuntamento dopo aver ottenuto nome, cognome, motivo e data/ora preferita.",
            "parameters": {
                "type": "object",
                "properties": {
                    "nome_cognome": {"type": "string", "description": "Nome e cognome del paziente"},
                    "motivo": {"type": "string", "description": "Motivo della visita o trattamento"},
                    "data_ora": {"type": "string", "description": "Data e/o orario preferito per l'appuntamento"}
                },
                "required": ["nome_cognome", "motivo", "data_ora"]
            }
        }
    }
]

# --- LETTURA DATABASE CLINICA ---
def carica_dati_clinica(id_clinica="dental_care_demo"):
    try:
        with open("database_cliniche.json", "r", encoding="utf-8") as f:
            database = json.load(f)
            return database.get(id_clinica, {})
    except Exception as e:
        print(f"❌ Errore caricamento database_cliniche.json: {e}")
        return {}

def genera_system_prompt(dati_clinica):
    servizi_str = "\n".join([f"- {s['nome']}: {s['prezzo']}" for s in dati_clinica.get("servizi", [])])
    regole_str = "\n".join([f"- {r}" for r in dati_clinica.get("regole_assistente", [])])

    prompt = (
        f"Sei l'assistente virtuale ufficiale della clinica '{dati_clinica.get('nome')}'.\n\n"
        f"INFORMAZIONI CLINICA:\n"
        f"- Indirizzo: {dati_clinica.get('indirizzo')}\n"
        f"- Telefono: {dati_clinica.get('telefono')}\n"
        f"- Orari: {dati_clinica.get('orari')}\n"
        f"- Urgenze: {dati_clinica.get('gestione_urgenze')}\n\n"
        f"LISTINO SERVIZI:\n{servizi_str}\n\n"
        f"REGOLE:\n{regole_str}\n"
        f"- Quando un paziente vuole prenotare, richiedi Nome, Cognome, Motivo e Data/Ora preferita.\n"
        f"- Quando hai TUTTE queste informazioni, usa lo strumento 'salva_prenotazione' per registrare la richiesta."
    )
    return {"role": "system", "content": prompt}

# ==============================================================================
# 🌐 ROTTE WEB (CARICAMENTO DEL TUO INDEX.HTML)
# ==============================================================================

def leggi_prenotazioni():
    if os.path.exists("prenotazioni.json"):
        try:
            with open("prenotazioni.json", "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return []
    return []

@app.route('/')
@app.route('/dashboard')
def mostra_sito():
    # Carica la lista aggiornata delle prenotazioni dal file JSON
    prenotazioni = leggi_prenotazioni()
    # Serve il tuo vero file templates/index.html passando le prenotazioni
    return render_template('index.html', prenotazioni=prenotazioni)

@app.route('/api/prenotazioni', methods=['GET'])
def api_prenotazioni():
    # API JSON per eventuali integrazioni o aggiornamenti via JavaScript
    return jsonify(leggi_prenotazioni())

# ==============================================================================
# 📩 WEBHOOK WHATSAPP (TWILIO)
# ==============================================================================

@app.route('/whatsapp-webhook', methods=['POST'])
def whatsapp_webhook():
    messaggio_utente = request.form.get('Body', '').strip()
    numero_mittente = request.form.get('From', '')
    
    print(f"\n📩 Messaggio da {numero_mittente}: {messaggio_utente}")

    dati_clinica = carica_dati_clinica("dental_care_demo")
    system_prompt = genera_system_prompt(dati_clinica)

    if numero_mittente not in cronologia_chat:
        cronologia_chat[numero_mittente] = []

    cronologia_chat[numero_mittente].append({"role": "user", "content": messaggio_utente})

    if len(cronologia_chat[numero_mittente]) > 10:
        cronologia_chat[numero_mittente] = cronologia_chat[numero_mittente][-10:]

    messaggi_per_groq = [system_prompt] + cronologia_chat[numero_mittente]

    try:
        response = client.chat.completions.create(
            model="llama-3.3-70b-versatile",
            messages=messaggi_per_groq,
            tools=tools,
            tool_choice="auto"
        )

        response_message = response.choices[0].message
        tool_calls = response_message.tool_calls

        if tool_calls:
            for tool_call in tool_calls:
                if tool_call.function.name == "salva_prenotazione":
                    args = json.loads(tool_call.function.arguments)
                    risultato_salvataggio = salva_prenotazione(
                        numero_telefono=numero_mittente,
                        nome_cognome=args.get("nome_cognome"),
                        motivo=args.get("motivo"),
                        data_ora=args.get("data_ora")
                    )
                    
                    messaggi_per_groq.append(response_message)
                    messaggi_per_groq.append({
                        "tool_call_id": tool_call.id,
                        "role": "tool",
                        "name": "salva_prenotazione",
                        "content": risultato_salvataggio
                    })

            second_response = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messaggi_per_groq
            )
            risposta_ia = second_response.choices[0].message.content
        else:
            risposta_ia = response_message.content

        cronologia_chat[numero_mittente].append({"role": "assistant", "content": risposta_ia})

    except Exception as e:
        print(f"❌ Errore durante l'elaborazione: {e}")
        risposta_ia = "Ci dispiace, si è verificato un problema momentaneo. Riprova tra poco!"

    resp = MessagingResponse()
    resp.message(risposta_ia)

    print(f"🤖 Risposta inviata al paziente:\n{risposta_ia}\n")

    return str(resp), 200, {'Content-Type': 'text/xml'}

# --- AVVIO SERVER ---
def avvia_tunnel():
    try:
        ngrok.set_auth_token("3Gv2JTPhMS3MNUGdZq1hd7A2JiS_76oGLMkSatX6k8kZPrRDV")
        listener = ngrok.forward(5000)
        print("\n" + "="*60)
        print("🚀 LINK WEBHOOK PER TWILIO:")
        print(f"{listener.url()}/whatsapp-webhook")
        print("🌐 SITO WEB E DASHBOARD APERTI SU:")
        print(f"http://127.0.0.1:5000")
        print("="*60 + "\n")
    except Exception as e:
        print(f"❌ Errore Ngrok: {e}")
from flask import jsonify

# Aggiungi questa rotta dentro il tuo file app.py
@app.route('/api/live-stats', methods=['GET'])
def live_stats():
    return jsonify({
        "revenue": 4850.00,
        "transactions": 14
    })
if __name__ == '__main__':
    # threading.Thread(target=avvia_tunnel, daemon=True).start()
    app.run(port=5000, debug=True, use_reloader=False)