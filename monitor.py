import os
import json
import requests
from bs4 import BeautifulSoup
import anthropic

# ============================================================
# CONFIGURAZIONE — credenziali prese dai Secrets di GitHub
# ============================================================
TELEGRAM_TOKEN   = os.environ["TELEGRAM_TOKEN"]
TELEGRAM_CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# URL della pagina da monitorare
URL_ALBO = "https://www.unipa.it/amministrazione/arearisorseumane/settorereclutamentoeselezioni/PersonaleTA/indeterminato.html"

# File dove salviamo i bandi già visti (la "memoria" dello script)
FILE_MEMORIA = "bandi_visti.json"

# ============================================================
# IL TUO PROFILO — qui descrivi chi sei all'agente AI
# Aggiorna questo blocco se qualcosa cambia nel tuo CV
# ============================================================
PROFILO_CANDIDATO = """
Sono Benedetto Francesco Marino, nato il 15/10/1994, cittadino italiano.

TITOLI DI STUDIO:
- Diploma di maturità classica (75/100)
- Laurea Triennale in Studi Filosofici e Storici, UniPa (110/110 con lode)
- Laurea Magistrale in Scienze Filosofiche e Storiche, UniPa (110/110 con lode)
- Master di II livello in Gestione e Sviluppo delle Risorse Umane, UniPa (100/100 con lode)

CERTIFICAZIONI LINGUISTICHE:
- Inglese C2 certificato (Gatehouse Awards / IESOL, Ofqual regulated, febbraio 2024)
- Francese B1

ESPERIENZA PROFESSIONALE (settore privato, ~4 anni totali):
- HR Junior Recruiter & Administration, Adecco Italia S.p.A. (6 mesi)
- HR Analyst, Mangia's Resorts (18 mesi, gestione 1500+ dipendenti su Zucchetti, payroll, L&D, ESG, welfare)
- HR Senior Specialist / HR Manager in apprendistato, Giglio.com S.p.A. (da ottobre 2023 a oggi, 250+ HC, recruiting, payroll, KPI, sviluppo organizzativo)
- Docente formazione professionale freelance, forIT S.r.l. (12 mesi, orientamento, bilancio competenze, didattica)

COMPETENZE DIGITALI:
- Suite MS Office e Google (Word, Excel con pivot e CERCA.VERT, SharePoint, Forms)
- Gestionali HR: Zucchetti HR (approfondito), AS400 (approfondito), SAP (base)
- Piattaforme FAD e videoconferencing

PUBBLICAZIONI:
- Traduttore ufficiale di articolo accademico, UniPa Press 2021

ALTRO:
- Nessuna esperienza nella Pubblica Amministrazione
- Nessuna appartenenza a categorie protette (L.68/99)
- Patenti A e B
- Nessuna condanna penale
"""

# ============================================================
# FUNZIONE 1 — Legge la pagina e raccoglie i bandi
# ============================================================
def ottieni_bandi():
    """
    Scarica la pagina HTML dell'albo UniPa e ne estrae
    i titoli dei bandi con i relativi link al PDF.
    Restituisce una lista di dizionari: [{titolo, url_pdf}, ...]
    """
    risposta = requests.get(URL_ALBO, timeout=30)
    risposta.raise_for_status()  # se la pagina non risponde, lancia un errore

    # BeautifulSoup è una libreria che "legge" l'HTML come se fosse un documento
    soup = BeautifulSoup(risposta.text, "html.parser")

    bandi = []
    # Cerchiamo tutti i titoli H1, H2, H3, H4 che contengono "D.D.G." o "Selezione"
    # che è il pattern dei titoli dei bandi su questa pagina
    for tag in soup.find_all(["h1", "h2", "h3", "h4"]):
        testo = tag.get_text(strip=True)
        if "D.D.G." in testo or "Selezione pubblica" in testo or "Concorso pubblico" in testo:
            # Cerchiamo il link al PDF del bando nella sezione successiva
            url_pdf = None
            # Scoriamo i fratelli (elementi HTML allo stesso livello) dopo il titolo
            for fratello in tag.find_next_siblings():
                link = fratello.find("a", href=lambda h: h and ".pdf" in h.lower() and "bando" in h.lower())
                if link:
                    href = link["href"]
                    # Alcuni link sono relativi (iniziano con /), li rendiamo assoluti
                    if href.startswith("/"):
                        href = "https://www.unipa.it" + href
                    url_pdf = href
                    break
                # Ci fermiamo quando troviamo il prossimo titolo
                if fratello.name in ["h1", "h2", "h3", "h4"]:
                    break

            bandi.append({
                "titolo": testo,
                "url_pdf": url_pdf
            })

    return bandi

# ============================================================
# FUNZIONE 2 — Carica e salva la memoria
# ============================================================
def carica_memoria():
    """Legge il file JSON con i titoli dei bandi già notificati."""
    try:
        with open(FILE_MEMORIA, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []

def salva_memoria(bandi_visti):
    """Salva la lista aggiornata dei bandi già notificati."""
    with open(FILE_MEMORIA, "w", encoding="utf-8") as f:
        json.dump(bandi_visti, f, ensure_ascii=False, indent=2)

# ============================================================
# FUNZIONE 3 — Scarica il PDF del bando
# ============================================================
def scarica_pdf(url):
    """
    Scarica il PDF del bando e lo restituisce come bytes (dati binari).
    Restituisce None se qualcosa va storto.
    """
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        return r.content  # .content = dati binari (il PDF grezzo)
    except Exception as e:
        print(f"Impossibile scaricare il PDF: {e}")
        return None

# ============================================================
# FUNZIONE 4 — Analisi AI del bando con Claude
# ============================================================
def analizza_bando_con_ai(titolo_bando, pdf_bytes):
    """
    Manda il PDF a Claude e chiede un'analisi strutturata.
    Restituisce il testo dell'analisi.
    
    Come funziona tecnicamente:
    - Convertiamo il PDF in base64 (un modo per trasmettere dati binari come testo)
    - Lo mandiamo all'API di Anthropic insieme al profilo del candidato
    - Claude lo legge e risponde con l'analisi
    """
    import base64

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    # Se non abbiamo il PDF, facciamo un'analisi solo sul titolo
    if pdf_bytes is None:
        prompt = f"""
Analizza questo bando di concorso UniPa basandoti solo sul titolo, 
poiché il PDF non è disponibile.

TITOLO: {titolo_bando}

PROFILO CANDIDATO:
{PROFILO_CANDIDATO}

Fornisci:
1. Una stima della categoria (B/C/D/EP)
2. Una stima dei requisiti probabili
3. Una valutazione preliminare di compatibilità
4. Raccomandazione: vale la pena approfondire?
"""
        messaggio = client.messages.create(
            model="claude-sonnet-4-5",
            max_tokens=1000,
            messages=[{"role": "user", "content": prompt}]
        )
        return messaggio.content[0].text

    # Con il PDF disponibile, analisi completa
    pdf_base64 = base64.standard_b64encode(pdf_bytes).decode("utf-8")

    prompt = f"""
Sei un assistente esperto in concorsi pubblici universitari italiani.
Analizza il seguente bando dell'Università di Palermo e valuta la compatibilità 
con il profilo del candidato.

PROFILO CANDIDATO:
{PROFILO_CANDIDATO}

ISTRUZIONI:
Leggi attentamente il bando PDF allegato e rispondi in modo strutturato con:

1. RIEPILOGO BANDO
   - Categoria e area
   - Numero posti
   - Scadenza domanda
   - Sede/destinazione

2. REQUISITI DI AMMISSIONE
   - Titolo di studio richiesto
   - Certificazioni richieste (lingua, informatica, etc.)
   - Requisiti specifici particolari
   - Ci sono riserve di posti? (categorie protette, interni, etc.)

3. ANALISI COMPATIBILITÀ
   Per ogni requisito, indica chiaramente: ✅ SODDISFATTO / ⚠️ DA VERIFICARE / ❌ NON SODDISFATTO
   Sii preciso e non ottimista: se manca qualcosa, dillo chiaramente.

4. VALUTAZIONE TITOLI VALUTABILI
   Indica quali titoli del candidato possono dare punteggio aggiuntivo 
   nella valutazione dei titoli (se prevista dal bando).

5. VERDETTO FINALE
   🟢 CANDIDATURA CONSIGLIATA / 🟡 CANDIDATURA POSSIBILE CON RISERVE / 🔴 NON COMPATIBILE
   Spiega brevemente il motivo in 2-3 righe.
"""

    messaggio = client.messages.create(
        model="claude-sonnet-4-5",
        max_tokens=1500,
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
                    "text": prompt
                }
            ]
        }]
    )

    return messaggio.content[0].text

# ============================================================
# FUNZIONE 5 — Invia il messaggio Telegram
# ============================================================
def invia_telegram(testo):
    """
    Manda un messaggio al tuo Telegram tramite le API ufficiali.
    Telegram ha un limite di 4096 caratteri per messaggio,
    quindi se il testo è più lungo lo spezziamo in più parti.
    """
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"

    # Spezziamo il testo in chunk da 4000 caratteri (margine di sicurezza)
    chunk_size = 4000
    chunks = [testo[i:i+chunk_size] for i in range(0, len(testo), chunk_size)]

    for chunk in chunks:
        payload = {
            "chat_id": TELEGRAM_CHAT_ID,
            "text": chunk,
            "parse_mode": "Markdown"  # permette testo in grassetto, corsivo, etc.
        }
        risposta = requests.post(url, json=payload, timeout=10)
        risposta.raise_for_status()

# ============================================================
# PROGRAMMA PRINCIPALE
# ============================================================
def main():
    print("Avvio monitoraggio bandi UniPa...")

    # 1. Carica la memoria (bandi già visti in precedenza)
    bandi_visti = carica_memoria()
    print(f"Bandi già in memoria: {len(bandi_visti)}")

    # 2. Scarica la lista attuale dei bandi dalla pagina UniPa
    bandi_attuali = ottieni_bandi()
    print(f"Bandi trovati sulla pagina: {len(bandi_attuali)}")

    # 3. Trova i bandi nuovi (non presenti nella memoria)
    nuovi_bandi = [b for b in bandi_attuali if b["titolo"] not in bandi_visti]
    print(f"Bandi nuovi da analizzare: {len(nuovi_bandi)}")

    if not nuovi_bandi:
        print("Nessun nuovo bando. Uscita.")
        return

    # 4. Per ogni nuovo bando: analizza e notifica
    for bando in nuovi_bandi:
        print(f"\nAnalisi: {bando['titolo'][:80]}...")

        # Scarica il PDF se disponibile
        pdf_bytes = None
        if bando["url_pdf"]:
            print(f"  Scarico PDF: {bando['url_pdf']}")
            pdf_bytes = scarica_pdf(bando["url_pdf"])

        # Analisi AI
        print("  Analisi con Claude...")
        analisi = analizza_bando_con_ai(bando["titolo"], pdf_bytes)

        # Costruisci il messaggio Telegram
        link_pdf = f"\n\n📄 [Apri bando PDF]({bando['url_pdf']})" if bando["url_pdf"] else ""
        messaggio = f"""🔔 *NUOVO BANDO UNIPA*

*{bando['titolo'][:200]}*
{link_pdf}

---
{analisi}

---
🔗 [Pagina completa]({URL_ALBO})"""

        # Invia su Telegram
        print("  Invio notifica Telegram...")
        invia_telegram(messaggio)

        # Aggiungi alla memoria
        bandi_visti.append(bando["titolo"])

    # 5. Salva la memoria aggiornata
    salva_memoria(bandi_visti)
    print(f"\nCompletato. Memoria aggiornata con {len(bandi_visti)} bandi totali.")

# Punto di ingresso dello script
if __name__ == "__main__":
    main()
