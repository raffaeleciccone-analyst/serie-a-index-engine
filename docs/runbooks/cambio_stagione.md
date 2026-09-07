# Runbook — Cambio di stagione

> Da eseguire quando la **giornata 3** della stagione nuova e' in archivio su
> Understat. Prima di allora la classifica ha una o due giornate e non regge
> nemmeno la nota che la accompagna.
>
> **Quando succede non sta scritto qui**: lo dice `python sentinella.py --tutte
> --stato`, che legge il calendario. Una data ricopiata in un runbook e' vera
> il giorno che la si scrive e sbagliata al primo rinvio — ed e' la stessa
> ragione per cui nessuna pagina del sito ha una stagione scritta a mano.

## 0 · Quando: lo dice la sentinella, che legge il calendario

Una data segnata a mano sbaglia appena una partita viene rinviata, e un
promemoria che arriva quando la condizione non e' vera insegna a ignorare i
promemoria. Quindi non si ricorda: si legge il calendario, ogni giorno.

```bash
python sentinella.py --tutte --stato        # legge e stampa, non manda niente
python sentinella.py --tutte                # e se e' ora, scrive su Telegram
python sentinella.py --bot                  # il bot risponde? come si chiama?
```

Da Understat arriva il calendario intero della stagione — tutte le partite, con
data e con l'indicazione di quali sono gia' state giocate. Da li' escono le due
cose che servono:

* **quante giornate hanno giocato tutte.** Non "quante partite ci sono": con i
  turni infrasettimanali e i rinvii una giornata resta aperta per giorni, e
  pubblicare in quella finestra vuol dire mettere in classifica squadre con una
  partita in meno delle altre — lo sbilanciamento che l'indice esiste per
  correggere, rimesso dentro dal calendario. La giornata di una squadra e' la
  sua ennesima partita in ordine di data, non il numero del turno.
* **quando si chiude quella che manca**, cioe' la data dell'ultima partita di
  quel turno.

Da cui **due messaggi**, ognuno una volta sola per stagione e per lega:

| Quando | Cosa dice |
|---|---|
| tre giorni prima (`--preavviso ORE`, default 72) | "la giornata 3 si chiude domenica 6 settembre alle 17:30" |
| quando la giornata e' in archivio | "si puo' pubblicare", col comando `pubblica.py` dentro |

`logs/sentinella.json` tiene il conto di cosa e' gia' partito. Un giro andato a
vuoto — Understat muto, rete assente — non segna niente: domani si riprova, e
l'avviso non si perde.

Una volta al giorno basta. Per pianificarla su Windows, da un prompt come
amministratore:

```
schtasks /create /tn "Sentinella stagione" ^
  /tr "C:\dev\serie-a-index-engine\sentinella.bat" /sc daily /st 09:00
```

### Preparare il bot, una volta sola

```bash
python sentinella.py --configura
```

Chiede il token, verifica che Telegram lo accetti, ti cerca fra chi ha scritto
al bot, scrive le due righe in `.env` senza toccare il resto del file, manda un
messaggio di prova e stampa il comando `schtasks` gia' pronto. Restano a te le
due cose che deve fare una persona: `/newbot` su **@BotFather**, e premere
**Avvia** nella chat col bot.

Il secondo passaggio non e' un dettaglio: **un bot Telegram non puo' scrivere
per primo a nessuno.** Finche' non gli scrivi tu non ha un destinatario, e la
lista delle chat resta vuota — non perche' e' configurato male.

A mano sarebbero: `/newbot` → `TELEGRAM_BOT_TOKEN` in `.env` → scrivi al bot →
`python sentinella.py --chat-id` → `TELEGRAM_CHAT_ID` in `.env`. Poi `--bot`
per vedere che risponda e `--prova` per farsi mandare un messaggio.

`.env` non e' versionato: il token resta sulla macchina. Il modello di tutte le
variabili, con le spiegazioni, e' in `.env.example`.

## 0-bis · La decisione, per non rimetterla in discussione ogni agosto

Il sito **si apre sulla stagione nuova**. Uno che a settembre apre su maggio
sembra abbandonato, e la prima pagina e' quella che vede chi arriva da un link.

Ma la stagione nuova **si presenta per quello che e'**: le pagine dichiarano
quante giornate ha giocato, la pillola del selettore porta "· in corso", e dove
la giornata coincide con un vintage misurato la nota cita il rho di quel punto
(0,29 Serie A / 0,23 Premier alla terza). Aprire sulla nuova tacendo
contraddirebbe la pagina di validazione, che quel numero lo pubblica.

Non c'e' niente da cambiare nel codice per ottenerlo: lo decide il payload
(`stagione_in_corso`, `giornate_totali`), e a maggio si spegne da solo.

## 1 · La sequenza, una lega alla volta

**In un comando solo:**

```bash
python pubblica.py --lega premier --stagione 2026-27            # elenca e basta
python pubblica.py --lega premier --stagione 2026-27 --esegui   # lo fa
python pubblica.py --lega premier --stagione 2026-27 --esegui --avvisa   # e ti scrive
```

Senza `--esegui` non tocca niente: stampa i passi che farebbe, in ordine, con
il comando esatto. Se uno si ferma, dice come riprendere da li' senza rifare il
resto (`--da <passo>`). Alla fine controlla che tutte le pagine pubblicate
dicano la stessa stagione — che e' il solo punto in cui qualcuno le confronta
fra loro: ogni script guarda solo la propria.

Con `--avvisa` l'esito arriva su Telegram quando ha finito, o quando si e'
fermato e su quale passo. Il giro dura parecchi minuti — `parte4` scarica una
stagione intera — e nessuno resta a guardarlo. Se il bot non e' configurato il
messaggio salta e la pubblicazione va avanti lo stesso: e' un avviso, non un
passo della procedura.

### Oppure: non lanciarlo affatto

La sentinella gira ogni mattina alle 9 e sa gia' riconoscere il momento. Con
`--pubblica` non ti scrive il comando: lo esegue.

```bash
python sentinella.py --tutte --giornate 3 --pubblica
```

E' quello che fa `sentinella.bat`, cioe' l'attivita' pianificata di Windows.
Quindi il cambio di stagione, in pratica, succede da solo: la mattina dopo che
la giornata 3 e' in archivio arrivano due messaggi — «ci penso io» e, qualche
minuto dopo, «e' pronta». Le due leghe partono per conto loro, perche' le
giornate non chiudono lo stesso giorno.

**Si ferma prima del commit e del push.** Le pagine sono scritte e verificate,
ma il sito va online quando lo decidi tu: e' la riga che separa "il computer mi
ha risparmiato mezz'ora" da "il computer ha pubblicato una cosa che nessuno ha
guardato". Restano i due passi 12 e 13 della tabella qui sotto.

Due cose da sapere:

* **gira solo su questa macchina**, perche' il database e' qui. E' anche il
  motivo per cui la sentinella in cloud, quando la riaccenderai, restera'
  quella che avvisa e basta;
* **un giro fallito non si segna**: se Understat e' muto o la rete cade a
  meta', domani mattina ci riprova da solo, e intanto il messaggio ti dice a
  che passo si e' fermato. Solo il giro riuscito viene segnato, e da li' in poi
  la sentinella sta zitta.

Il controllo si puo' anche chiedere da solo, su un sito gia' pubblicato:

```bash
python pubblica.py --lega premier --solo-verifica
```

Quello che `pubblica.py` fa, passo per passo, e' qui sotto — perche' una
procedura automatica di cui non si sa cosa fa e' peggio di una a mano. Le due
variabili decidono tutto il resto — database, cartella di uscita, repo, lingua,
nome del sito:

```bash
export SERIE_A_LEGA="ENG-Premier League"   # oppure "ITA-Serie A"
export SERIE_A_SEASON=2026-27
```

| # | Comando | Cosa fa |
|---|---|---|
| 1 | `python parte4_aggiorna.py` | scarica e carica la stagione nuova |
| 2 | `python deriva_game_log.py --season 2026-27 --esegui` | game log per squadra |
| 3 | `python anagrafica_da_hexi.py --esegui` | rose e ruoli della stagione nuova |
| 4 | archivio (vedi sotto) | mette al sicuro la stagione conclusa |
| 5 | `python parte1_analisi.py` | l'indice della stagione nuova |
| 6 | `python parte1_analisi.py --tutte-le-stagioni` | la vista aggregata |
| 7 | `python parte2_dashboard.py` | la classifica |
| 8 | `python pagina_home.py` | la homepage |
| 9 | `python pagina_guida.py` | il metodo (nav + prime sei) |
| 10 | `python pagina_squadra.py` | le pagine squadra, e toglie le retrocesse |
| 11 | `python parte3_valida_tpi.py --solo-pagina` | riscrive la validazione **senza rimisurare** |
| 12 | link al CSV nel `README.md` del repo del sito | l'unica riga a mano rimasta |
| 13 | `git add -A && git commit && git push` nel repo del sito | pubblica |

**Il passo 4**, da fare *prima* del passo 5, perche' il 5 sovrascrive
`payload.json`:

```bash
cd dashboard_output          # dashboard_output_eng per la Premier
cp payload.json       payload_2025-26.json
cp payload_lista.json payload_lista_2025-26.json
cp payload_full.json  payload_full_2025-26.json
```

> Al 3/9/2026 le copie ci sono gia' in tutte e due le cartelle: il passo 4 e'
> fatto. Rifarlo dopo il passo 5 archivierebbe la stagione nuova al posto di
> quella conclusa.

## 2 · Cosa NON si fa

**Non si rilancia `parte3_valida_tpi.py` senza `--solo-pagina`.** Le quindici
verifiche confrontano l'indice di meta' strada con la classifica di fine
stagione: su una stagione di tre giornate la "fine" e' la terza giornata, e
ognuna misurerebbe se stessa. Ne uscirebbe una pagina piena di numeri altissimi
e senza senso, che sovrascrive quelli veri in `validazione_sintesi.json`. Lo
script si ferma da solo, ma la strada giusta e' `--solo-pagina`: riscrive la
pagina dai risultati salvati in `validazione_dati.json`, senza toccare un
numero. Serve perche' anche la validazione porta la barra con la stagione
dentro, e senza questo passo sarebbe l'unica pagina a dire ancora "25/26".

**Non si lancia `parte1_analisi.py --top-n 0`.** `payload_full.json` e i dodici
vintage `payload_g*.json` sono la stagione conclusa, ed e' su quelli che gira
il backtest. Le copie di sicurezza sono in `payload_full_2025-26.json`.

Da settembre `payload_full.json` e' quindi di un'altra stagione rispetto a
`payload.json`: la homepage e le pagine squadra se ne accorgono da sole e
leggono il payload pubblicato. Non c'e' niente da spostare a mano.

## 3 · Le guardie, e cosa fare quando parlano

| Messaggio | Significa | Cosa fare |
|---|---|---|
| `Stagione incoerente: payload.json e' 2026-27, SERIE_A_SEASON e' 2025-26` | i dati e l'etichetta della barra parlano di due stagioni | esportare la variabile giusta e rilanciare |
| `calendario: le partite scaricate risultano in {...}` | l'ingestione ha etichettato male le partite | non proseguire: e' il difetto del DEFAULT di colonna, va guardato il database |
| `Understat non ha restituito nessuna partita` | download muto o stagione non cominciata | riprovare piu' tardi, **non** forzare |
| `Solo N pagine squadra scritte: le altre restano dove sono` | il payload ha meno di dieci squadre | il giro non e' completo, guardare a monte |
| `payload_full.json e' la stagione X e il sito pubblica la Y` | normale da settembre a maggio | niente |

## 4 · Come si controlla che sia andata bene

Sul repo del sito, dopo il giro:

```bash
grep -c 'in corso' dashboard_<lega>.html      # la pillola dice che e' in corso
grep -o '<small>[0-9/]*</small>' index.html   # la barra dice la stagione nuova
git status --short                            # le retrocesse compaiono come cancellate
```

E a occhio, sulla homepage: la prima cifra deve dire "qualificati dopo N
giornate", non "giocatori qualificati".

## 5 · Aperto

* **I `ruolo_override` in `parte1_analisi.py`: chiuso il 7/9/2026.** Erano 71,
  scritti sulle rose 2025-26 della Serie A (sulle altre leghe l'elenco e'
  disattivato). Misurati contro `heXI/data/normalized/SA_2026-2027.json`: 20
  concordavano, 15 contraddicevano, **36 erano di giocatori usciti dal
  campionato** — non inerti, ma omonimi in attesa: e' cosi' che Leon Bailey e'
  diventato un difensore sulla Premier. I 36 sono stati tolti; restano 35.
  Mlacic, Kouadio e Palma, forzati a POR ma difensori per heXI e senza un tiro
  in carriera, sono tornati DIF.

  Il difetto vero era un altro: **`POR` non era un ruolo, era un interruttore
  di spegnimento**, perche' i portieri escono dall'indice in `main()`. Cinque
  dei tredici forzati a POR tiravano e segnavano (Darmian 2022' e 3 gol, Viti
  2811', Okereke, Hysaj, Iling-Junior). Adesso le due cose sono separate:
  `ruolo_override` corregge un ruolo, `escludi` (nome -> motivo, applicato da
  `applica_esclusioni`) toglie dall'indice, il motivo finisce nel log e un nome
  che non trova nessuno viene detto invece che ignorato. `escludi` nasce
  **vuoto**: nessuno dei cinque ci e' stato travasato. Test in
  `tests/regression/test_esclusioni.py`.
* **`caso-mercato.html`: chiuso il 7/9/2026.** Non era una decisione, era un
  posto sbagliato: `caso_mercato.py` stava dentro `serie-a-index`, cioe' dentro
  il repo del sito pubblicato, quindi nessuno lo chiamava e al cambio di
  stagione la pagina restava indietro da sola con "25/26" scritto a mano nella
  barra. Ora il generatore sta nel motore, la barra viene da `config`
  (`SITO_MARCHIO`, `SEASON_ETICHETTA_BREVE`) ed e' il passo 12 della sequenza,
  **solo per la Serie A**.

  Il caso vuole almeno 1500 minuti a testa (~17 partite intere): sull'annata in
  corso nessuno ci arriva, quindi `caso_mercato.py` **sceglie da solo l'ultima
  stagione che ha abbastanza minuti** e la pagina lo dichiara in cima ("i dati
  sono della 2025/26, conclusa; si rifara' sulla 2026/27 quando ne avra'
  abbastanza"). `--elenca` dice cosa potrebbe raccontare, `--stagione` forza.
  Una stagione senza minuti si rifiuta invece di uscire vuota. Test in
  `tests/regression/test_caso_mercato.py`.

* **`dashboard_pro.html`: niente da fare, verificato il 7/9/2026.** Legge solo
  `payload.json` — che la sequenza riscrive — e la barra e' un segnaposto
  (`data-st-it="{curBreve}"`) riempito a runtime dal payload, dal commit
  `fa547f0`. Segue la stagione da sola, ed e' per questo che la verifica di
  `pubblica.py` la esenta dal confronto sull'etichetta.
* Il **DEFAULT `'2025-26'`** della colonna `season` e' ancora nel DDL dei due
  database. La verifica a valle dell'ingestione lo copre; toglierlo tocca
  `set_up_tpi_pro/aggiorna.py`, che e' un flusso a parte.
