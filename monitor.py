import os
import json
import time
import requests
from bs4 import BeautifulSoup
import anthropic

TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

URL_ALBO     = "https://www.unipa.it/amministrazione/arearisorseumane/settorereclutamentoeselezioni/PersonaleTA/indeterminato.html"
FILE_MEMORIA = "bandi_visti.json"
PAUSA_TRA_ANALISI = 65

PROFILO_CANDIDATO = """
Benedetto Francesco Marino, nato 15/10/1994, cittadino italiano.

TITOLI DI STUDIO:
- Diploma maturità classica (75/100)
- Laurea Triennale in Studi Filosofici e Storici, UniPa (110/110 con lode)
- Laurea Magistrale in Scienze Filosofiche e Storiche, UniPa (110/110 con lode)
- Master II livello in Gestione e Sviluppo delle Risorse Umane, UniPa (100/100 con lode)

CERTIFICAZIONI LINGUISTICHE:
- Inglese C2 certificato (Gatehouse Awards / IESOL, Ofqual, febbraio 2024)
- Francese B1

ESPERIENZA PROFESSIONALE (~4 anni, settore privato HR):
- HR Junior Recruiter & Administration, Adecco Italia S.p.A. (6 mesi)
- HR Analyst, Mangia's Resorts (18 mesi — payroll, L&D, ESG, welfare, 1500+ dipendenti su Zucchetti)
- HR Senior Specialist / HR Manager in apprendistato, Giglio.com S.p.A. (da ottobre 2023 — 250+ HC, recruiting, payroll, KPI, sviluppo organizzativo)
- Docente formazione professionale freelance, forIT S.r.l. (12 mesi)

COMPETENZE DIGITALI:
- MS Office avanzato (Excel: pivot, CERCA.VERT; Word, Forms)
- Gestionali HR: Zucchetti HR (avanzato), AS400 (avanzato), SAP (base)
- Piattaforme FAD e videoconferencing

PUBBLICAZIONI:
- Traduttore ufficiale articolo accademico, UniPa Press 2021

ALTRO:
- Nessuna esperienza nella Pubblica Amministrazione
- Nessuna categoria protetta L.68/99
- Patenti A e B
"""

def ottieni_bandi():
    risposta = requests.get(URL_ALBO, timeout=30)
    risposta.raise_for_status()
    soup = BeautifulSoup(risposta.text, "html.parser")
    bandi = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        testo = tag.get_text(strip=True)
        if "D.D.G." in testo or "Selezione pubblica" in testo or "Concorso pubblico" in testo:
            url_pdf = None
            for fratello in tag.find_next_siblings():
                link = fratello.find("a", href=lambda h: h and ".pdf" in h.lower() and "bando" in h.lower())
                if link:
                    href = link["href"]
                    if href.startswith("/"):
                        href = "https://www.unipa.it" + href
                    url_pdf = href
                    break
                if fratello.name in ["h1", "h2", "h3", "h4"]:
                    break
            bandi.append({"titolo": testo, "url_pdf": url_pdf})
    return bandi

def carica_memoria():
    try:
        with open(FILE_MEMORIA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def salva_memoria(bandi_visti):
    with open(FILE_MEMORIA, "w", encoding="utf-8") as f:
        json.dump(bandi_visti, f, ensure_ascii=False, indent=2)

def scarica_pdf(url):
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  PDF non scaricabile: {e}")
        return None

def analizza_bando_con_ai(titolo_bando, pdf_bytes):
    import base64
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    prompt_base = f"""
Sei un assistente esperto in concorsi pubblici universitari italiani.
Analizza il bando e produci ESCLUSIVAMENTE il testo del messaggio Telegram,
senza introduzioni, senza commenti, senza nulla prima o dopo.

PROFILO CANDIDATO:
{PROFILO_CANDIDATO}

REGOLE FONDAMENTALI:
- NON usare asterischi, underscore, cancelletti o qualsiasi markdown
- Usa solo testo semplice ed emoji
- Massimo 700 parole
- Il verdetto va SEMPRE in cima, prima di tutto il resto
- NON scrivere testi tranciati o parole interrotte per mancanza di caratteri, 
il messaggio deve risultare quanto più professionale possibile

CALCOLO SCADENZA (importante):
- Cerca nel bando la data di pubblicazione all'Albo o sulla Gazzetta Ufficiale
- Calcola la scadenza sulla base delle informazioni contenute nel bando, è fondamentale essere il più preciso possibile
- Calcola la data esatta tenendo conto dei giorni del mese
- Se la data di pubblicazione non e' nel PDF, scrivi "vedi bando"
- Indica sempre: "Pubblicato il [data] - Scadenza il [data contenuta o calcolata] ore 12:00"

STRUTTURA OBBLIGATORIA (rispettala esattamente):

[riga separatrice: ━━━━━━━━━━━━━━━]
VERDETTO: [CANDIDATURA CONSIGLIATA / CON RISERVE / NON COMPATIBILE] [emoji 🟢/🟡/🔴]
[una riga di motivazione sintetica]
[riga separatrice: ━━━━━━━━━━━━━━━]

RIEPILOGO
Categoria: [es. D - Area amministrativo-gestionale]
Posti: [numero]
[Pubblicato il X - Scadenza il Y ore 12:00]
Sede: [destinazione specifica e/o reparto specifico se indicato]

REQUISITI CHIAVE
[elenca solo requisiti non banali: titolo studio specifico, certificazioni,
esperienza minima, conoscenze tecniche particolari]
[NON elencare mai: eta 18+, cittadinanza, idoneita fisica, assenza condanne,
obbligo leva, assenza parentele con Rettore — sono sempre soddisfatti]

COMPATIBILITA
[per ogni requisito non banale: emoji + requisito + valutazione in una riga]
✅ = soddisfatto pienamente
⚠️ = soddisfatto parzialmente o con riserve
❌ = non soddisfatto

TITOLI VALUTABILI
[se il bando prevede valutazione titoli: elenca quali titoli del candidato
possono dare punteggio aggiuntivo e stima approssimativa se possibile]
[se non prevista valutazione titoli: scrivi "Non prevista valutazione titoli"]

MATERIE D'ESAME
[elenca sinteticamente le materie su cui vertono le prove, una per riga]
[utile per capire cosa studiare in caso di candidatura]
"""

    if pdf_bytes is None:
        prompt = prompt_base + f"\n\nTITOLO BANDO (PDF non disponibile, analizza solo dal titolo):\n{titolo_bando}"
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=900,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text

    pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=900,
        messages=[{
            "role": "user",
            "content": [
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": pdf_base64
                    }
                },
                {
                    "type": "text",
                    "text": prompt_base
                }
            ]
        }]
    )
    return msg.content[0].text

def invia_telegram(testo):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    chunk_size = 4000
    chunks = [testo[i:i+chunk_size] for i in range(0, len(testo), chunk_size)]
    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk
        }
        risposta = requests.post(url, json=payload, timeout=10)
        risposta.raise_for_status()

def main():
    print("Avvio monitoraggio bandi UniPa...")

    bandi_visti = carica_memoria()
    print(f"Bandi gia in memoria: {len(bandi_visti)}")

    bandi_attuali = ottieni_bandi()
    print(f"Bandi trovati sulla pagina: {len(bandi_attuali)}")

    nuovi_bandi = [b for b in bandi_attuali if b["titolo"] not in bandi_visti]
    print(f"Bandi nuovi da analizzare: {len(nuovi_bandi)}")

    if not nuovi_bandi:
        print("Nessun nuovo bando. Uscita.")
        return

    for i, bando in enumerate(nuovi_bandi):
        print(f"\n[{i+1}/{len(nuovi_bandi)}] {bando['titolo'][:80]}...")

        pdf_bytes = None
        if bando["url_pdf"]:
            print("  Scarico PDF...")
            pdf_bytes = scarica_pdf(bando["url_pdf"])

        print("  Analisi con Claude...")
        analisi = analizza_bando_con_ai(bando["titolo"], pdf_bytes)

        link_pdf = f"📄 Bando PDF: {bando['url_pdf']}" if bando["url_pdf"] else ""

        messaggio = f"""🔔 NUOVO BANDO UNIPA

{bando['titolo']}

{link_pdf}

{analisi}

🔗 Tutti i bandi: {URL_ALBO}"""

        print("  Invio Telegram...")
        invia_telegram(messaggio)

        bandi_visti.append(bando["titolo"])
        salva_memoria(bandi_visti)

        if i < len(nuovi_bandi) - 1:
            print(f"  Attendo {PAUSA_TRA_ANALISI}s...")
            time.sleep(PAUSA_TRA_ANALISI)

    print(f"\nCompletato. {len(nuovi_bandi)} bandi notificati.")

if __name__ == "__main__":
    main()
