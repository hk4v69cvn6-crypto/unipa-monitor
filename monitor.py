import os
import json
import time
import datetime
import re
import requests
from bs4 import BeautifulSoup
import anthropic

# ============================================================
# CONFIGURAZIONE
# ============================================================
TELEGRAM_TOKEN    = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID  = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

URL_ALBO      = "https://www.unipa.it/amministrazione/arearisorseumane/settorereclutamentoeselezioni/PersonaleTA/indeterminato.html"
FILE_MEMORIA  = "bandi_visti.json"
FILE_OFFSET   = "telegram_offset.json"
PAUSA_TRA_ANALISI = 65

# ============================================================
# PROFILO CANDIDATO
# ============================================================
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
- MS Office avanzato (Excel: pivot, CERCA.VERT; Word, SharePoint, Forms)
- Gestionali HR: Zucchetti HR (avanzato), AS400 (avanzato), SAP (base)
- Piattaforme FAD e videoconferencing

PUBBLICAZIONI:
- Traduttore ufficiale articolo accademico, UniPa Press 2021

ALTRO:
- Nessuna esperienza nella Pubblica Amministrazione
- Nessuna categoria protetta L.68/99
- Patenti A e B
"""

# ============================================================
# UTILITÀ
# ============================================================
def estrai_codice(titolo):
    """Estrae il numero DDG dal titolo. Es: '14778' da 'D.D.G. n. 14778/2025...'"""
    match = re.search(r'n\.\s*(\d+)', titolo)
    return match.group(1) if match else titolo[:15].replace(" ", "")

# ============================================================
# MEMORIA (con migrazione automatica dal vecchio formato)
# ============================================================
def carica_memoria():
    try:
        with open(FILE_MEMORIA, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not data:
            return []
        # Migrazione automatica: vecchio formato era lista di stringhe
        if isinstance(data[0], str):
            print("  Migrazione memoria al nuovo formato...")
            return [{
                "titolo":           t,
                "codice":           estrai_codice(t),
                "url_pdf":          None,
                "data_rilevamento": datetime.date.today().isoformat(),
                "verdetto":         "❓",
                "categoria":        "N/D",
                "scadenza":         "N/D",
                "seguito":          False,
                "documenti_visti":  []
            } for t in data]
        return data
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def salva_memoria(bandi):
    with open(FILE_MEMORIA, "w", encoding="utf-8") as f:
        json.dump(bandi, f, ensure_ascii=False, indent=2)

def titoli_in_memoria(memoria):
    """Gestisce sia il nuovo formato (lista di dict) che il vecchio (lista di stringhe)."""
    return {b["titolo"] if isinstance(b, dict) else b for b in memoria}

# ============================================================
# OFFSET TELEGRAM (evita di riprocessare comandi già letti)
# ============================================================
def carica_offset():
    try:
        with open(FILE_OFFSET, "r") as f:
            return json.load(f).get("offset", 0)
    except (FileNotFoundError, json.JSONDecodeError):
        return 0

def salva_offset(offset):
    with open(FILE_OFFSET, "w") as f:
        json.dump({"offset": offset}, f)

# ============================================================
# SCRAPING (con scadenza dalla pagina e tutti i PDF)
# ============================================================
def ottieni_bandi():
    """
    Scarica la pagina e per ogni bando estrae:
    - titolo
    - url_pdf: PDF principale del bando
    - tutti_i_pdf: tutti i PDF nella sezione (inclusi avvisi, esiti, convocazioni)
    - scadenza_pag: scadenza letta direttamente dalla pagina (es. "22/01/2026 alle ore 12:00")
    """
    risposta = requests.get(URL_ALBO, timeout=30)
    risposta.raise_for_status()
    soup = BeautifulSoup(risposta.text, "html.parser")

    bandi = []
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        testo = tag.get_text(strip=True)
        if "D.D.G." in testo or "Selezione pubblica" in testo or "Concorso pubblico" in testo:
            url_pdf_principale = None
            tutti_i_pdf        = []
            scadenza_pagina    = None

            for fratello in tag.find_next_siblings():
                testo_fratello = fratello.get_text(" ", strip=True)

                # Cerca la scadenza nel testo dell'elemento
                if not scadenza_pagina and "scadenza" in testo_fratello.lower():
                    match = re.search(
                        r'[Ss]cadenza\s+(\d{1,2}[/\-]\d{1,2}[/\-]\d{4}'
                        r'(?:\s+alle\s+ore\s+\d{1,2}:\d{2})?)',
                        testo_fratello
                    )
                    if match:
                        scadenza_pagina = match.group(1).strip()

                # Raccogli tutti i PDF della sezione
                links = fratello.find_all("a", href=lambda h: h and ".pdf" in h.lower())
                for link in links:
                    href = link["href"]
                    if href.startswith("/"):
                        href = "https://www.unipa.it" + href
                    if href not in tutti_i_pdf:
                        tutti_i_pdf.append(href)
                    if url_pdf_principale is None and "bando" in href.lower():
                        url_pdf_principale = href

                if fratello.name in ["h1", "h2", "h3", "h4"]:
                    break

            bandi.append({
                "titolo":       testo,
                "url_pdf":      url_pdf_principale,
                "tutti_i_pdf":  tutti_i_pdf,
                "scadenza_pag": scadenza_pagina
            })

    return bandi

# ============================================================
# DOWNLOAD PDF
# ============================================================
def scarica_pdf(url):
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content
    except Exception as e:
        print(f"  PDF non scaricabile: {e}")
        return None

# ============================================================
# ANALISI AI
# ============================================================
def analizza_bando_con_ai(titolo_bando, pdf_bytes, scadenza_nota=None):
    """
    Analizza il bando con Claude.
    Se scadenza_nota è disponibile (letta dalla pagina), la passa
    direttamente senza chiedere all'AI di calcolarla.
    """
    import base64
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    if scadenza_nota:
        istruzione_scadenza = (
            f"SCADENZA (letta direttamente dalla pagina UniPa, usala senza modifiche): "
            f"{scadenza_nota}"
        )
        scadenza_riepilogo = scadenza_nota
    else:
        istruzione_scadenza = (
            "SCADENZA: cerca nel bando la data di pubblicazione all'Albo o sulla "
            "Gazzetta Ufficiale e calcola con precisione la data esatta "
            "(tieni conto dei giorni del mese). Se non trovata scrivi: vedi bando"
        )
        scadenza_riepilogo = "[data calcolata] ore 12:00"

    prompt_base = f"""
Sei un assistente esperto in concorsi pubblici universitari italiani.
Analizza il bando e produci ESATTAMENTE questo output, senza nulla prima o dopo.

PRIMA RIGA OBBLIGATORIA (non modificare il formato):
META|VERDETTO|CATEGORIA|SCADENZA

Dove:
- VERDETTO e' una di: CONSIGLIATA, RISERVE, NON_COMPATIBILE
- CATEGORIA es: D - Area amministrativo-gestionale
- SCADENZA: {scadenza_nota if scadenza_nota else "data GG/MM/AAAA oppure N/D"}

POI UNA RIGA VUOTA, POI IL MESSAGGIO TELEGRAM.

PROFILO CANDIDATO:
{PROFILO_CANDIDATO}

{istruzione_scadenza}

CRITERI VERDETTO (rispettali con rigore assoluto):

NON_COMPATIBILE solo se presente almeno una barriera oggettiva che determina
ESCLUSIONE AUTOMATICA dalla procedura selettiva:
- Il bando richiede una laurea in discipline che il candidato NON possiede
  (es. medicina, giurisprudenza, ingegneria — lui ha filosofia e master HR)
- I posti sono riservati ESCLUSIVAMENTE a categorie protette L.68/99 o a
  dipendenti interni — un esterno non puo' proprio partecipare
- Richiede madrelingua in una lingua diversa dall'italiano
- Richiede un'abilitazione professionale specifica non posseduta
  (es. abilitazione forense, iscrizione a ordine professionale)
- Qualsiasi altro requisito la cui mancanza comporta esclusione per regolamento

RISERVE se il candidato puo' presentare domanda ma:
- Manca di esperienza specifica (PA, ricerca, ambiti tecnici particolari)
- Le materie d'esame richiedono studio in aree non coperte dal CV
- Il profilo e' distante ma non escluso

CONSIGLIATA se:
- Tutti i requisiti di ammissione sono soddisfatti
- Il profilo e' ragionevolmente allineato con le attivita' richieste

ATTENZIONE ASSOLUTA:
La mancanza di esperienza specifica NON e' mai motivo di NON_COMPATIBILE.
NON_COMPATIBILE significa impossibilita' oggettiva di partecipare, non difficolta'.
In caso di dubbio tra NON_COMPATIBILE e RISERVE, scegli sempre RISERVE.

REGOLE FONDAMENTALI:
- NON usare asterischi, underscore, cancelletti o qualsiasi markdown
- Usa solo testo semplice ed emoji
- Massimo 350 parole per il messaggio Telegram (esclusa la riga META)
- NON troncare mai il testo: ogni sezione deve essere completa e professionale
- Il verdetto va SEMPRE in cima, prima di tutto il resto

STRUTTURA OBBLIGATORIA DEL MESSAGGIO TELEGRAM:

━━━━━━━━━━━━━━━
VERDETTO: [CANDIDATURA CONSIGLIATA / CON RISERVE / NON COMPATIBILE] [🟢/🟡/🔴]
[una riga di motivazione sintetica]
━━━━━━━━━━━━━━━

RIEPILOGO
Categoria: [es. D - Area amministrativo-gestionale]
Posti: [numero]
Scadenza: {scadenza_riepilogo}
Sede: [destinazione specifica e/o reparto specifico se indicato]

REQUISITI CHIAVE
[elenca solo requisiti non banali: titolo studio specifico, certificazioni,
esperienza minima richiesta, conoscenze tecniche particolari]
[NON elencare mai: eta 18+, cittadinanza, idoneita fisica, assenza condanne,
obbligo leva, parentele con Rettore — non servono]

COMPATIBILITA
[per ogni requisito non banale: emoji + requisito + valutazione in una riga]
✅ = soddisfatto pienamente
⚠️ = soddisfatto parzialmente o con riserve
❌ = non soddisfatto (solo per barriere oggettive di esclusione)

TITOLI VALUTABILI
[se il bando prevede valutazione titoli: elenca quali titoli del candidato
possono dare punteggio aggiuntivo con stima approssimativa]
[se non prevista: scrivi "Non prevista valutazione titoli"]

MATERIE D'ESAME
[materie delle prove, una per riga — utili per capire cosa studiare]
"""

    if pdf_bytes is None:
        prompt = prompt_base + f"\n\nTITOLO BANDO (PDF non disponibile):\n{titolo_bando}"
        msg = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1800,
            messages=[{"role": "user", "content": prompt}]
        )
        return msg.content[0].text

    pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")
    msg = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1800,
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
                {"type": "text", "text": prompt_base}
            ]
        }]
    )
    return msg.content[0].text

def parse_risposta_ai(risposta_raw):
    """
    Separa la riga META dal messaggio Telegram.
    Restituisce (meta_dict, testo_telegram).
    """
    righe = risposta_raw.strip().split("\n")
    meta  = {"verdetto": "❓", "categoria": "N/D", "scadenza": "N/D"}
    testo = risposta_raw

    if righe and righe[0].startswith("META|"):
        parti = righe[0].split("|")
        if len(parti) >= 4:
            v = parti[1].strip()
            meta["verdetto"]  = ("🟢" if v == "CONSIGLIATA" else
                                 "🟡" if v == "RISERVE" else
                                 "🔴" if v == "NON_COMPATIBILE" else "❓")
            meta["categoria"] = parti[2].strip()
            meta["scadenza"]  = parti[3].strip()
        # Rimuovi riga META e riga vuota successiva
        testo = "\n".join(righe[2:]).strip()

    return meta, testo

# ============================================================
# INVIO TELEGRAM
# ============================================================
def invia_telegram(testo):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    if len(testo) <= 3800:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": testo}
        requests.post(url, json=payload, timeout=10).raise_for_status()
        return

    # Divide solo ai fine riga, mai a metà di una parola o URL
    chunks = []
    while len(testo) > 3800:
        split_pos = testo.rfind('\n', 0, 3800)
        if split_pos == -1:
            split_pos = testo.rfind(' ', 0, 3800)
        if split_pos == -1:
            split_pos = 3800
        chunks.append(testo[:split_pos])
        testo = testo[split_pos:].lstrip('\n')
    if testo:
        chunks.append(testo)

    for chunk in chunks:
        payload = {"chat_id": TELEGRAM_CHAT_ID, "text": chunk}
        requests.post(url, json=payload, timeout=10).raise_for_status()

# ============================================================
# COMANDI TELEGRAM
# I comandi vengono elaborati una volta al giorno durante il run.
# /segui CODICE  — inizia a monitorare aggiornamenti di un bando
# /smetti CODICE — smette di monitorarlo
# /seguiti       — lista bandi seguiti
# ============================================================
def processa_comandi(memoria):
    offset = carica_offset()
    url    = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"

    try:
        r       = requests.get(url, params={"offset": offset, "timeout": 5}, timeout=10)
        r.raise_for_status()
        updates = r.json().get("result", [])
    except Exception as e:
        print(f"  Errore lettura comandi: {e}")
        return memoria

    if not updates:
        print("  Nessun nuovo comando Telegram.")
        return memoria

    nuovo_offset = updates[-1]["update_id"] + 1

    for update in updates:
        msg   = update.get("message", {})
        testo = msg.get("text", "").strip()
        if not testo.startswith("/"):
            continue

        parti   = testo.split()
        comando = parti[0].lower()

        if comando == "/segui" and len(parti) > 1:
            codice  = parti[1]
            trovato = False
            for b in memoria:
                if isinstance(b, dict) and b.get("codice") == codice:
                    b["seguito"] = True
                    trovato = True
                    invia_telegram(
                        f"✅ Ora seguo il bando {codice}:\n"
                        f"{b['titolo'][:120]}\n\n"
                        f"Scadenza: {b.get('scadenza', 'N/D')}\n\n"
                        f"Riceverai una notifica ogni volta che vengono "
                        f"pubblicati nuovi documenti (convocazioni, esiti, avvisi).\n"
                        f"Per smettere: /smetti {codice}"
                    )
                    break
            if not trovato:
                invia_telegram(
                    f"⚠️ Bando con codice {codice} non trovato in memoria.\n\n"
                    f"Usa il numero DDG visibile nel titolo del bando.\n"
                    f"Esempio: per 'D.D.G. n. 14778/2025' scrivi:\n"
                    f"/segui 14778"
                )

        elif comando == "/smetti" and len(parti) > 1:
            codice  = parti[1]
            trovato = False
            for b in memoria:
                if isinstance(b, dict) and b.get("codice") == codice:
                    b["seguito"] = False
                    trovato = True
                    invia_telegram(f"🔕 Ho smesso di seguire il bando {codice}.")
                    break
            if not trovato:
                invia_telegram(f"⚠️ Bando con codice {codice} non trovato.")

        elif comando == "/seguiti":
            seguiti = [b for b in memoria if isinstance(b, dict) and b.get("seguito")]
            if not seguiti:
                invia_telegram(
                    "Non stai seguendo nessun bando al momento.\n\n"
                    "Per seguire un bando scrivi:\n"
                    "/segui CODICE_DDG\n\n"
                    "Il codice e' il numero dopo 'D.D.G. n.' nel titolo.\n"
                    "Es: /segui 14778"
                )
            else:
                testo_r = f"📌 BANDI CHE STAI SEGUENDO ({len(seguiti)})\n\n"
                for b in seguiti:
                    n_doc = len(b.get("documenti_visti", []))
                    testo_r += f"[{b['codice']}] {b['titolo'][:80]}...\n"
                    testo_r += f"Categoria: {b.get('categoria','N/D')}\n"
                    testo_r += f"Scadenza: {b.get('scadenza','N/D')}\n"
                    testo_r += f"Documenti monitorati: {n_doc}\n\n"
                testo_r += "Per smettere di seguire un bando:\n/smetti CODICE_DDG"
                invia_telegram(testo_r)

    salva_offset(nuovo_offset)
    return memoria

# ============================================================
# CONTROLLO AGGIORNAMENTI BANDI SEGUITI
# ============================================================
def controlla_aggiornamenti(memoria, bandi_attuali):
    """
    Per ogni bando seguito, verifica se sono comparsi nuovi PDF
    (avvisi, convocazioni, esiti) rispetto all'ultima scansione.
    """
    mappa   = {b["titolo"]: b for b in bandi_attuali}
    trovati = False

    for bando_mem in memoria:
        if not isinstance(bando_mem, dict) or not bando_mem.get("seguito"):
            continue

        bando_attuale = mappa.get(bando_mem["titolo"])
        if not bando_attuale:
            continue

        pdf_visti   = set(bando_mem.get("documenti_visti", []))
        pdf_attuali = set(bando_attuale.get("tutti_i_pdf", []))
        nuovi_pdf   = pdf_attuali - pdf_visti

        if nuovi_pdf:
            trovati = True
            print(f"  Aggiornamento trovato: {bando_mem['titolo'][:60]}...")
            messaggio = (
                f"🔔 AGGIORNAMENTO BANDO SEGUITO\n\n"
                f"[{bando_mem['codice']}] {bando_mem['titolo']}\n\n"
                f"Nuovi documenti pubblicati:\n"
            )
            for url in nuovi_pdf:
                nome = url.split("/")[-1]
                messaggio += f"• {nome}\n  {url}\n"
            invia_telegram(messaggio)
            bando_mem["documenti_visti"] = list(pdf_attuali)

    if not trovati:
        print("  Nessun aggiornamento sui bandi seguiti.")

    return memoria

# ============================================================
# RECAP DOMENICALE
# ============================================================
def invia_recap_domenicale(memoria):
    oggi    = datetime.date.today()
    seguiti = [b for b in memoria if isinstance(b, dict) and b.get("seguito")]
    verdi   = [b for b in memoria if isinstance(b, dict) and b.get("verdetto") == "🟢"]
    gialli  = [b for b in memoria if isinstance(b, dict) and b.get("verdetto") == "🟡"]
    rossi   = [b for b in memoria if isinstance(b, dict) and b.get("verdetto") == "🔴"]
    altri   = [b for b in memoria if isinstance(b, dict) and
               b.get("verdetto") not in ["🟢", "🟡", "🔴"]]

    # Scadenze entro 7 giorni
    scadenze_vicine = []
    for b in memoria:
        if not isinstance(b, dict):
            continue
        scadenza = b.get("scadenza", "N/D")
        if not scadenza or scadenza == "N/D" or "vedi" in scadenza.lower():
            continue
        try:
            match = re.search(r'(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})', scadenza)
            if match:
                g, m, a = int(match.group(1)), int(match.group(2)), int(match.group(3))
                data_sc = datetime.date(a, m, g)
                delta   = (data_sc - oggi).days
                if 0 <= delta <= 7:
                    scadenze_vicine.append((b, delta, data_sc))
        except Exception:
            pass
    scadenze_vicine.sort(key=lambda x: x[2])

    msg  = f"📋 RECAP SETTIMANALE BANDI UNIPA\n"
    msg += f"Domenica {oggi.strftime('%d/%m/%Y')}\n"
    msg += "━━━━━━━━━━━━━━━\n\n"

    if seguiti:
        msg += f"📌 BANDI CHE STAI SEGUENDO ({len(seguiti)})\n"
        for b in seguiti:
            n_doc = len(b.get("documenti_visti", []))
            msg += f"• [{b['codice']}] {b['titolo'][:70]}...\n"
            msg += f"  {b.get('categoria','N/D')} | Scad: {b.get('scadenza','N/D')}\n"
            msg += f"  Documenti monitorati: {n_doc}\n"
        msg += "\n"

    if scadenze_vicine:
        msg += "⚠️ SCADENZE QUESTA SETTIMANA\n"
        for b, delta, data in scadenze_vicine:
            quando = ("OGGI" if delta == 0 else
                      "DOMANI" if delta == 1 else
                      f"tra {delta} giorni")
            msg += f"• {b['titolo'][:65]}...\n"
            msg += f"  {data.strftime('%d/%m')} ore 12:00 ({quando})\n"
        msg += "\n"

    if verdi:
        msg += f"🟢 CANDIDABILI ({len(verdi)})\n"
        for b in verdi:
            msg += f"• {b.get('categoria','N/D')} | Scad: {b.get('scadenza','N/D')}\n"
            msg += f"  {b['titolo'][:70]}...\n"
        msg += "\n"

    if gialli:
        msg += f"🟡 CON RISERVE ({len(gialli)})\n"
        for b in gialli:
            msg += f"• {b.get('categoria','N/D')} | Scad: {b.get('scadenza','N/D')}\n"
            msg += f"  {b['titolo'][:70]}...\n"
        msg += "\n"

    if rossi:
        msg += f"🔴 NON COMPATIBILI ({len(rossi)}) — nessun dettaglio\n\n"

    if altri:
        msg += (f"❓ BANDI SENZA ANALISI ({len(altri)}) — "
                f"verranno analizzati al prossimo aggiornamento\n\n")

    msg += f"🔗 {URL_ALBO}"
    invia_telegram(msg)

# ============================================================
# PROGRAMMA PRINCIPALE
# ============================================================
def main():
    oggi = datetime.date.today()
    print(f"Avvio monitoraggio bandi UniPa — {oggi.isoformat()}...")

    # 1. Carica memoria e processa comandi Telegram ricevuti
    memoria = carica_memoria()
    print(f"Bandi in memoria: {len(memoria)}")
    print("Controllo comandi Telegram...")
    memoria = processa_comandi(memoria)

    # 2. Scarica lista bandi attuale dalla pagina
    bandi_attuali = ottieni_bandi()
    print(f"Bandi sulla pagina: {len(bandi_attuali)}")

    # 3. Controlla aggiornamenti sui bandi seguiti
    print("Controllo aggiornamenti bandi seguiti...")
    memoria = controlla_aggiornamenti(memoria, bandi_attuali)

    # 4. Trova bandi nuovi (non ancora in memoria)
    titoli_visti = titoli_in_memoria(memoria)
    nuovi_bandi  = [b for b in bandi_attuali if b["titolo"] not in titoli_visti]
    print(f"Bandi nuovi da analizzare: {len(nuovi_bandi)}")
    
    # PROTEZIONE CREDITI: se troppi bandi "nuovi", la memoria è stata resettata
    # Salva i titoli senza chiamare l'AI e avvisa
    if len(nuovi_bandi) > 3:
        print(f"  ATTENZIONE: {len(nuovi_bandi)} bandi nuovi rilevati. Probabile reset memoria.")
        print(f"  Salvo i titoli senza analisi AI per proteggere i crediti.")
        invia_telegram(
            f"⚠️ ATTENZIONE\n\n"
            f"Rilevati {len(nuovi_bandi)} bandi 'nuovi' — probabile reset della memoria.\n\n"
            f"I titoli sono stati salvati senza analisi AI per proteggere i crediti.\n"
            f"Verifica bandi_visti.json nel repository, poi avvia manualmente il workflow."
        )
        for bando in nuovi_bandi:
            memoria.append({
                "titolo":           bando["titolo"],
                "codice":           estrai_codice(bando["titolo"]),
                "url_pdf":          bando["url_pdf"],
                "data_rilevamento": oggi.isoformat(),
                "verdetto":         "❓",
                "categoria":        "N/D",
                "scadenza":         bando.get("scadenza_pag") or "N/D",
                "seguito":          False,
                "documenti_visti":  bando.get("tutti_i_pdf", [])
            })
        salva_memoria(memoria)
        return

    for i, bando in enumerate(nuovi_bandi):
        print(f"\n[{i+1}/{len(nuovi_bandi)}] {bando['titolo'][:80]}...")

        # Scarica PDF principale
        pdf_bytes = None
        if bando["url_pdf"]:
            print("  Scarico PDF...")
            pdf_bytes = scarica_pdf(bando["url_pdf"])

        # Analisi AI (passa la scadenza già nota dalla pagina)
        print("  Analisi con Claude...")
        risposta_raw         = analizza_bando_con_ai(
            bando["titolo"],
            pdf_bytes,
            bando.get("scadenza_pag")
        )
        meta, testo_telegram = parse_risposta_ai(risposta_raw)

        # La scadenza definitiva: priorità alla pagina, fallback alla AI
        scadenza_finale = (
            bando.get("scadenza_pag") or
            (meta["scadenza"] if meta["scadenza"] != "N/D" else "N/D")
        )

        # Salva in memoria con formato arricchito
        record = {
            "titolo":           bando["titolo"],
            "codice":           estrai_codice(bando["titolo"]),
            "url_pdf":          bando["url_pdf"],
            "data_rilevamento": oggi.isoformat(),
            "verdetto":         meta["verdetto"],
            "categoria":        meta["categoria"],
            "scadenza":         scadenza_finale,
            "seguito":          False,
            "documenti_visti":  bando.get("tutti_i_pdf", [])
        }
        memoria.append(record)
        # Salva subito: se il run si interrompe non si rianalizzano i già processati
        salva_memoria(memoria)

        # Costruisci e invia messaggio Telegram
        link_pdf  = f"📄 Bando PDF: {bando['url_pdf']}" if bando["url_pdf"] else ""
        messaggio = (
            f"🔔 NUOVO BANDO UNIPA\n\n"
            f"{bando['titolo']}\n\n"
            f"{link_pdf}\n\n"
            f"{testo_telegram}\n\n"
            f"Per seguire gli aggiornamenti di questo bando:\n"
            f"/segui {record['codice']}\n"
            f"(i comandi vengono elaborati una volta al giorno)\n\n"
            f"🔗 Tutti i bandi: {URL_ALBO}"
        )
        print("  Invio Telegram...")
        invia_telegram(messaggio)

        if i < len(nuovi_bandi) - 1:
            print(f"  Attendo {PAUSA_TRA_ANALISI}s...")
            time.sleep(PAUSA_TRA_ANALISI)

    # 5. Recap domenicale (solo se oggi è domenica)
    if oggi.weekday() == 6:
        print("\nOggi e' domenica — invio recap settimanale...")
        invia_recap_domenicale(memoria)

    salva_memoria(memoria)
    print(f"\nCompletato.")

if __name__ == "__main__":
    main()
