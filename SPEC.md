# Dovetail — SPEC v0.3

*Trovare la rivista giusta per un paper, partendo da titolo, abstract e word count.*
*Creato: 27 agosto 2026 — Giovanni Spitale + Ono*
*v0.3, 27 ago 2026: architettura a tre strati. Storia delle revisioni in §17.*

> **Lingua:** il codice, la CLI e i test sono **in inglese**; questo documento resta in italiano,
> perche e il posto dove si ragiona, nel registro del wiki. Dove la spec cita un identificatore,
> cita quello vero.

> **Il principio che regge l'architettura:** *fidarsi di OpenAlex per quello che contiene, non
> per quello che pensa.* L'inventario — ISSN, flag OA, il corpus dei lavori — è affidabile e
> insostituibile. La tassonomia dei topic è la sua opinione, ed è la parte che si sostituisce.

---

## 0. Cos'è, e cosa non è

Dovetail è un **tool web + MCP** che, dato un manoscritto (titolo, abstract, word count, e
opzionalmente il testo), produce una **shortlist ordinata di venue candidate**, ciascuna con il
tipo di articolo compatibile, il limite di parole, lo stato open access rispetto al finanziatore,
e — questo è il punto — **i criteri che la reggono, etichettati come di merito o logistici**.

Il nome è il giunto a coda di rondine: tiene perché le due forme combaciano, non perché è
incollato.

**Non è** un ranking di qualità delle riviste, non calcola impact factor come obiettivo, e non
decide. Espone il compromesso e lo lascia a chi scrive.

**E non è nemmeno un ranking, in senso stretto.** Un ranking ordina un insieme fisso secondo un
criterio stabile, e qui non succede nessuna delle due cose: il punteggio si calcola **contro il
singolo paper**, l'insieme è quello che lo stadio 2 è riuscito a pescare quel giorno, e il
denominatore («#447 su 3810») è un artefatto di quante candidate sono state recuperate, non una
posizione in classifica. Ciò che il tool produce è **una lista di riviste** con, accanto a ciascuna,
un punteggio dichiarato e i criteri che la reggono. Chiamarla classifica suggerirebbe un ordine
totale che non esiste, e inviterebbe a leggere la distanza fra la terza e la quarta come se
significasse qualcosa.

**Non promette la latenza attesa.** La v0.1 lo faceva; nessuna sorgente la fornisce. Vedi §8.

**Non duplica PaperTrail.** PaperTrail sa *dove sta* un paper e *dove è già stato*; Dovetail sa
*dove potrebbe andare*. Dovetail legge PaperTrail; non ci scrive.

**Non sostituisce il wiki.** La dottrina — l'anatomia di un paper, le corsie chiuse, cosa insegna
un desk reject — resta in `wiki/`. Dovetail tiene i fatti e produce la lista.

---

## 0b. Cosa non sta in questo repository, e perché

Il repository è pubblico. Il **caso di validazione** — quale manoscritto, e quali riviste lo hanno
rimbalzato — non ci sta dentro: è un paper **non pubblicato**, con coautori che non hanno acconsentito
a renderlo noto, e con una submission ancora aperta. Pubblicarlo sarebbe una decisione presa al posto
loro.

La linea è: **pubblicato = pubblico, non pubblicato = anonimo.** I paper già usciti e le riviste che
li hanno presi restano nominati, perché sono informazione pubblica e servono a rendere verificabili i
numeri della §16c. Il manoscritto sotto esame no.

In pratica: `validation/case.local.json` è gitignorato e contiene titolo, abstract e ruoli;
`validation/case.example.json` è committato e contiene un manoscritto inventato contro record di
riviste reali, così lo script gira per chiunque. Le fixture dei test portano nomi neutri, e i record
OpenAlex che contengono — quelli sì pubblici — non sono legati da nessuna parte a un rimbalzo.

---

## 1. Il problema, in una riga

La scelta della venue oggi si fa a memoria, e la memoria è fatta di rimbalzi. Nel 2026 tre paper
hanno accumulato cinque desk reject fra Venue A, Venue B e tre
generaliste, con latenze fra uno e sei giorni e quasi nessun commento di merito. Le lezioni sono
state scritte nel wiki *dopo*, una per volta.

Il modo di guasto ricorrente non è scegliere una brutta rivista, è **sbagliare famiglia**: mandare
a riviste di etica un testo che, misurato, è psicologia morale o salute pubblica.

---

## 2. Validazione retrodittiva (27 ago 2026) — cosa mostra davvero

Prima di scrivere codice, l'ipotesi è stata testata su un **caso reale**, che questo documento non
nomina: un manoscritto non pubblicato, tre tentativi, due desk reject. Le due riviste che l'hanno
rimbalzato compaiono come **Venue A** e **Venue B**; il caso per esteso sta in
`validation/case.local.json`, che non è nel repository. Vedi §0b.

**La v0.1 di questa sezione rivendicava più di quanto i dati sostengano.** Qui c'è la versione
corretta.

Abstract reale passato a `GET /text/topics`:

| score | topic | subfield | field |
|---|---|---|---|
| 0.997 | T11147 Misinformation and Its Impacts | Sociology and Political Science | Social Sciences |
| 0.989 | T10833 Vaccine Coverage and Hesitancy | Health | Social Sciences |
| 0.988 | T12520 Psychology of Moral and Emotional Judgment | Cognitive Neuroscience | Neuroscience |

Nessun topic di etica medica. Confronto contro otto riviste scelte a mano:

| rivista | overlap topic | overlap subfield | stato OA | esito reale |
|---|---|---|---|---|
| Journal of Moral Education | 0.0857 | 0.1826 | closed | mai provata |
| Social Science & Medicine | 0.0192 | 0.2389 | ibrida, APC 3800 | mai provata |
| Venue C | 0.0412 | 0.1060 | full OA, APC 3290 | **sottomesso** |
| Bioethics | 0.0229 | 0.1172 | ibrida, APC 4550 | mai provata |
| Medicine, Health Care and Philosophy | 0.0172 | 0.0713 | ibrida, APC 3290 | special issue cancellata |
| Journal of Medical Ethics | 0.0104 | 0.0531 | closed | mai provata |
| Venue A | 0.0000 | 0.0191 | closed | **desk reject** |
| Venue B | 0.0000 | 0.0140 | full OA, APC 2290 | **desk reject, «out of scope»** |

### Cosa mostra, e cosa no

**Lo zero è aritmetica, non misura.** Verificato: nessuno dei tre topic del testo compare nei 25
topic di Venue A né in quelli di Venue B. Nessun termine in comune,
quindi prodotto scalare nullo. Venue C condivide T10833.

Quindi il risultato **valida lo stadio 2 (generazione candidate), non lo stadio 3 (punteggio)**: le
due venue che hanno rimbalzato non sarebbero mai entrate in shortlist, perché il generatore per
topic non le produce. È un risultato più forte di quello rivendicato in v0.1, ma di un altro stadio.
**Il punteggio di scope resta non validato.**

**La separazione è grossolana.** Riordinando con otto pesature diverse, le due venue restano
settima e ottava in sette casi su otto. Il coefficiente non guida la conclusione, il che è buono,
ma dice anche che quasi tutta la separazione viene dal canale subfield, che su questo paniere
misura in pratica «quanto è di scienze sociali questa rivista». Quattro riviste di etica contro
quattro non di etica: il metro separa le famiglie, che è la diagnosi di §1, non un fit fine.

**Il dataset non contiene nessun esito positivo.** Due rifiuti, una submission in corso, una
special issue cancellata, quattro mai provate. Una validazione così può mostrare che i punteggi
bassi corrispondono a venue cattive; **non può mostrare che i punteggi alti corrispondano a venue
buone**. È il limite che decide se il ranking serve, ed è il motivo per cui §16 chiede un secondo
caso con almeno un esito positivo e con il paniere generato dal sistema.

**Altri limiti:** n=1; retrodizione; paniere scelto a mano; il punteggio di field non è normalizzato
(Journal of Moral Education esce a 1.1623 perché il profilo del testo somma score grezzi e quello
della rivista somma a 1 — da sostituire con il coseno, che non cambia l'esito).

Lo script che riproduce la tabella sta in `validation/`.

---

## 3. Architettura

Stesso stampo degli altri tool borant, e in particolare di GrantRadar, di cui Dovetail riprende la
grammatica: sorgenti con *hints* in prosa, coda di proposte, **approvazione solo nella UI**.

- FastAPI + SQLite, repo dedicato, deploy come gli altri MCP borant.
- **Mono-workspace**: nessun ACL, nessun parametro `workspace` nei tool MCP. Borant ID solo per il
  login alla UI. Se un giorno servirà a ITE si aggiunge un livello sopra; non si paga ora.
- **`dovetail.borant.eu`** — record DNS creato il 27 ago 2026.
- Porta **8015**, da confermare libera sul VPS al deploy (Contrarian 8014, LSSR 8013).
- Le quattro trappole note del deploy MCP borant valgono anche qui: lifespan, rotte `@pubbliche`,
  `PUBLIC_URL`, barra finale.

---

## 4. Modello dati

### `venue`
Campi da API (rinfrescabili in blocco) e campi da guidelines (uno alla volta, via coda).

| campo | fonte | note |
|---|---|---|
| `id`, `display_name`, `issn_l`, `issn[]` | OpenAlex | chiave `issn_l` |
| `host_organization_name` | OpenAlex | **`publisher` non esiste su `/sources`**, era un errore di v0.1 |
| `homepage_url`, `country_code`, `type` | OpenAlex | `country_code` nullo su ~105k riviste |
| `is_oa`, `is_in_doaj`, `apc_usd`, `apc_prices`, `oa_flip_year` | OpenAlex | `apc_usd` **nullo sul 92,7%** — vedi §8 |
| `oa_model` | derivato | quattro valori, non tre — vedi §8 |
| `works_count` | OpenAlex | |
| `h_index`, `2yr_mean_citedness` | OpenAlex `summary_stats` | annidati, non di primo livello |
| `topics[]` con `count` e `share` | OpenAlex | **troncato a 25 per ogni rivista** — vedi §6 |
| `corpus_embedding` | derivato | profilo di scope dagli ultimi ~200 abstract; porta **modello ed etichetta di versione**, perché cambiare modello invalida tutti i profili insieme |
| `corpus_sampled_at`, `corpus_n` | derivato | quando e su quanti lavori è stato costruito |
| `license[]`, `review_process`, `publication_time_weeks`, `has_waiver` | DOAJ | solo full OA |
| `anvur_class` | ANVUR | per settore, rilevante 11/C2 |
| `indexed_in[]` | NLM / Scopus | |
| `predatory_risk` | derivato + manuale | vedi §9 |
| `verified_at` **per campo** | — | vedi §10 |

### `venue_alias`
**Nuova in v0.2, e senza questa `exclude_venues` e `venue_history` non sono implementabili.**

Il vocabolario venue di PaperTrail è diciannove stringhe libere, senza ISSN, con refusi e maiuscole
incoerenti (`Medicine health care and philosopy`, `journal of moral education`). Serve una tabella
che tenga la risoluzione una volta che un umano l'ha confermata, altrimenti ogni consultazione
rifà lo stesso fuzzy match e sbaglia allo stesso modo.

`alias_string`, `venue_id`, `source_system` (`papertrail` | `doaj` | `manuale`), `confirmed_by`,
`confirmed_at`. Una risoluzione non confermata **non è un alias**: è una proposta in coda (§5).

### `article_type`
Un record per (venue, tipo). **Il dato che nessuna API porta.**

`venue_id`, `name`, `word_limit`, `word_limit_scope` (se il conteggio include o esclude abstract,
referenze, didascalie — la fonte più comune di errore), `abstract_limit`, `refs_limit`,
`figures_limit`, `unsolicited`, `source_url`, `verified_at`.

### `match_run`
`title`, `abstract`, `word_count`, `anatomy?`, `constraints`, `created_at`, più:

- **`text_profile`** — il profilo di topic calcolato, **persistito**. In v0.1 mancava, e senza di
  esso `explain_match` doveva rifare `/text/topics`, che costa (§5) e può essere bloccato.
- **`venue_snapshot`** — i profili di topic delle venue in shortlist al momento della corsa. I
  profili cambiano a ogni rinfresco: senza snapshot, tornare indietro dopo un esito confronta
  l'esito di ieri con i dati di oggi, che è esattamente ciò che la tabella esiste per evitare.

### `criterion`
Per ogni `match_result`: `kind` (`merito` | `logistica`), `label`, `weight`, `evidence` (da quale
campo viene). I pesi stanno in una tabella di configurazione versionata, non nel codice, e
`match_run` registra quale versione ha usato.

### `source` e `proposal`
`source`: `name`, `url`, `hints`, `kind` (`api` | `guidelines`), `enabled`.
`proposal`: `kind`, **`source_id` (FK verso `source`)**, `fields`, `rationale`, `confidence`,
`source_url`, `status`. In v0.1 mancava la FK, e senza di essa `list_sources()` esponeva sorgenti
che nessun tool consumava — una regressione rispetto a GrantRadar, dove `propose_grant` prende un
`source_id` e chiude il ciclo.

---

## 5. Sorgenti, e quanto costano

**Verificate alla fonte il 27 ago 2026, con i costi reali letti dagli header:**

| sorgente | costo | cosa dà | stato |
|---|---|---|---|
| OpenAlex `/sources` per ISSN | **1 credito** ($0.0001) | ISSN, editore, OA, APC, h-index, topics con conteggi | ✅ |
| OpenAlex `/sources?filter=` | **1 credito** | compone `topics.id` + `is_in_doaj` + `apc_usd:<N`; accetta `topics.id:T1\|T2` in **una** chiamata | ✅ è il generatore di candidate |
| OpenAlex `/text/topics` | **100 crediti** ($0.01) | classifica titolo+abstract | ✅ ma **tariffata** |
| OpenAlex `/works?search=` | **10 crediti** ($0.001) | possibile fallback di classificazione | ⚠️ non validata, vedi sotto |
| DOAJ `/api/search/journals` | gratis | APC, licenza, peer review, `publication_time_weeks`, waiver | ✅ |

### Il vincolo di budget, che va deciso in Fase 0

Il budget OpenAlex è **giornaliero e per account**, e si azzera a mezzanotte UTC:

- **anonimo** (solo `mailto`): **$0.10/giorno** = 10 classificazioni. Questa sessione di sviluppo
  l'ha esaurito.
- **account gratuito con chiave**: **$1/giorno** = 100 classificazioni.
- **crediti prepagati**: acquistabili a incrementi da $1, self-serve, scadono dopo tre mesi.
- abbonamenti annuali da $5.000 in su, fuori discussione e non necessari.

Quindi il rimedio non è architetturale: **serve una chiave di un account gratuito OpenAlex**, che
porta a cento consultazioni al giorno, e nel caso un dollaro di prepagato ne aggiunge altre cento.
Va comunque messo in cache il `text_profile` (§4) e va tenuto un contatore di budget con
degradazione dichiarata quando si avvicina al limite, invece di un 429 in faccia all'utente.

**Nota sul fallback via `/works`:** costa dieci volte meno di `/text/topics`, ma il test del 27 ago
con una query lunga ha restituito **un solo lavoro** e un profilo inutilizzabile. La direzione è
promettente e **non è validata**: prima di dipenderne va testata con `title_and_abstract.search` e
query più corte.

**Da verificare prima di dipenderne:**

| sorgente | cosa darebbe | stato |
|---|---|---|
| Sherpa Romeo | self-archiving, embarghi, versioni | chiave gratuita, non testata |
| Journal Checker Tool (cOAlition S) | conformità venue × finanziatore × istituzione | **`/api/` risponde 404**: nessuna API pubblica. Il vincolo SNSF si deriva da `oa_model`, con i limiti di §8 |
| ANVUR liste classe A | classe per settore concorsuale | fogli di calcolo, ingestione periodica a mano |
| NLM Catalog | indicizzazione MEDLINE/PubMed | non testata |

**Nota sugli endpoint `/text/*`:** il 27 ago `/text/keywords` ha risposto **500** mentre nella stessa
sequenza `/text/topics` e `/text/concepts` rispondevano 200, quindi il 500 era reale e non un budget
esaurito letto male. Più tardi, esaurito il budget, tutti e tre rispondono 429. Vanno distinti i due
casi in fase di gestione errori: 500 è l'endpoint rotto, 429 è la cassa vuota.

### I tre strati, e chi fa cosa

Nessuna delle tre sorgenti fa il lavoro delle altre, e confonderle è il modo più facile di
costruire la cosa sbagliata.

| strato | strumento | cosa fa | perché non lo fa un altro |
|---|---|---|---|
| **inventario** | OpenAlex | enumera le candidate, flag OA, ISSN, corpus | un LLM non ha l'inventario: presuppone di sapere già quali pagine aprire |
| **scope** | **embedding** sugli ultimi ~200 abstract di ogni rivista | quanto questo testo somiglia a ciò che la rivista pubblica | i topic sono venticinque slot troncati di una tassonomia generale, e non separano mai per genere o forma |
| **genere e forma** | Haiku 4.5 / Sonnet 5 sulle **sole finaliste** | word limit, article type, e il giudizio «che tipo di cosa è questa» | non sta in nessuna API, e nessun punteggio numerico lo produce |

**Perché gli embedding e non i topic.** Il profilo di scope si costruisce dagli abstract reali degli
ultimi lavori della rivista (OpenAlex li ha, un credito per pagina di risultati), non dalla
tassonomia. È continuo invece che categoriale, non ha il troncamento a 25, si riesegue sul nuovo
pubblicato, e non costa un LLM per rivista. I topic restano come segnale grosso allo stadio 2, dove
servono a enumerare e non a giudicare. **Non validato:** il budget OpenAlex era esaurito il giorno
in cui è stato scritto questo paragrafo. Va in Fase 1b (§16).

**Dove va l'LLM, e dove no.** Sulle 8-12 finaliste, mai sulle 259 candidate: lì sarebbe lento e
sprecato. Haiku 4.5 per l'estrazione meccanica dalle guidelines (output strutturato, `strict: true`);
**Sonnet 5 per il giudizio di genere**, che è la chiamata difficile e non va data al modello piccolo.

**Costo, ai prezzi del 27 ago 2026** (Haiku 4.5 $1/$5 per milione, Sonnet 5 $2/$10, Batch API al 50%):
guidelines più indice recente sono circa 15k token in ingresso e 500 in uscita, cioè **meno di due
centesimi a rivista** con Haiku. Seminare trecento riviste costa sull'ordine dei cinque dollari, due
e mezzo in batch. Rileggere dieci finaliste a ogni consultazione, meno di venti centesimi. Il
vincolo non è il prezzo: è non farlo girare sul posto sbagliato.

### Sorgenti di tipo guidelines: la cardinalità è diversa

GrantRadar ha ventitré sorgenti, curabili a mano, con hints scritti da un umano. Le author
guidelines sono **una pagina per rivista**, e trattarle «esattamente come i finanziatori» (v0.1) non
regge. Decisione: una `source` di `kind: guidelines` è **una riga per editore, non per rivista** —
gli hints descrivono dove Springer, Wiley, BMC, Elsevier tengono i limiti di parole e come sono
strutturate le loro pagine, che è stabile e riusabile su centinaia di riviste. La proposta porta
`source_id` (l'editore) e `source_url` (la pagina specifica letta).

---

## 6. Il matcher

Quattro stadi. **Modifica importante rispetto a v0.1:** i vincoli non si applicano più allo stadio 2.

**Stadio 1 — Profilo del testo.** `/text/topics` su titolo e abstract, persistito in `match_run`. Se
è dato il manoscritto, si calcola anche l'anatomia (§7).

*Guard-rail obbligatorio.* La classificazione è instabile su input corti: lo stesso paper, ridotto a
una frase, restituisce topic **disgiunti** da quelli dell'abstract completo (`Health and Conflict
Studies` invece di `Misinformation and Its Impacts`). Sotto una lunghezza minima, o se il topic
primario sta sotto una soglia di score, Dovetail **rifiuta di produrre una shortlist** e lo dice.
Vale la regola di §7: fallire dichiarando è accettabile, indovinare no.

**Stadio 2 — Generazione candidate.** `GET /sources?filter=topics.id:T1|T2|T3` in **una** chiamata
(v0.1 ne prevedeva una per topic: tre volte il costo per lo stesso risultato).

*Nessun vincolo qui.* In v0.1 i filtri duri stavano nella query API, il che annullava la regola più
enfatica della spec: una venue esclusa a monte non può essere «marcata da verificare», perché non è
mai entrata. Le candidate si generano sul solo scope; i vincoli agiscono allo stadio 4, dove sono
ispezionabili.

*Numeri reali*, sui tre topic di il caso con DOAJ e APC<3500: **70, 156, 33**, unione 259 con
duplicati. La v0.1 diceva «~30 per topic»: sbagliato.

**Stadio 3 — Punteggio di scope, per embedding.** Coseno fra l'embedding del testo e il profilo
della rivista, costruito dagli **ultimi ~200 abstract realmente pubblicati** e non dalla tassonomia.
È il cambio principale della v0.3: sostituisce la parte di OpenAlex di cui non ci si fida.

Il punteggio per topic ai tre livelli — topic, subfield, field — **resta**, calcolato col coseno e
riportato accanto, come secondo segnale e come diagnostica. Serve a vedere il caso «disciplina
giusta, argomento assente», che l'embedding da solo comprime in un numero. Due segnali che
divergono sono informazione, non rumore, e vanno mostrati.

*Avvertenza che l'embedding non risolve:* la somiglianza di contenuto **non è fit di genere**. Un
testo empirico e un saggio concettuale sullo stesso argomento hanno embedding vicini. Quella
distinzione la fa lo stadio 5, non questo.

*Avvertenza da dichiarare in output:* `topics[]` è **troncato a 25 per ogni rivista**,
indipendentemente dalla dimensione. Copertura misurata dell'output della rivista: BMC Public Health
92,3%, Social Science & Medicine 96,6%, **PLoS ONE 24,8%, Scientific Reports 19,7%**. Su una
generalista larga il profilo ignora tre quarti di ciò che pubblica e produce zeri falsi proprio
contro le venue più capaci di accogliere un lavoro interdisciplinare. Sopra una soglia di
`works_count` il punteggio di scope va marcato come inaffidabile. Inoltre `count` è multi-etichetta
(Venue B somma al 174%), quindi non confrontabile fra riviste senza normalizzazione.

**Stadio 4 — Vincoli, criteri, taglio.** I vincoli (§8) marcano ed escludono qui; ogni superstite
riceve i criteri etichettati (§9).

*Regola sul dato mancante, che v0.1 non aveva.* La v0.1 proteggeva il dato **scaduto** e ignorava il
dato **assente**, che è il caso maggioritario (`apc_usd` nullo sul 92,7%). Entrambi producono lo
stesso guasto. Regola unica: **un vincolo non esclude mai una venue il cui campo rilevante è
assente o scaduto**; la venue resta, marcata `da verificare`, e sale in cima alla coda di verifica.

*Taglio della shortlist*, assente in v0.1: massimo **dodici** venue, e comunque solo quelle sopra una
soglia minima di scope. Se meno di tre passano i vincoli, Dovetail restituisce anche le escluse
dicendo quale vincolo le ha tolte: una lista vuota non è una risposta.

**Stadio 5 — Lettura delle finaliste.** Nuovo in v0.3, e gira **solo** sulle venue sopravvissute al
taglio. Due chiamate di natura diversa:

- **Estrazione, con Haiku 4.5.** Legge le author guidelines e ne tira fuori article type, word
  limit, `word_limit_scope`, limiti di abstract e referenze, se accetta non sollecitati. Output
  strutturato con `strict: true`, perché è un lavoro meccanico e va vincolato. Ogni campo estratto
  **diventa una proposta in coda** con il suo `source_url`, mai una scrittura diretta: è lo stesso
  patto di GrantRadar, e vale anche quando a proporre è un modello.
- **Giudizio di genere, con Sonnet 5.** Legge l'abstract (o l'anatomia, se c'è) contro l'indice
  recente della rivista e risponde a una domanda sola: *questa rivista pubblica cose fatte così?* Non
  «di questo argomento», che l'hanno già detto gli stadi 3 e 4. È il criterio di merito che ai due
  desk reject del 2026 mancava, e va al modello grande perché è la chiamata difficile.

Il giudizio di genere **non riordina** la shortlist. Alza una bandiera accanto alla venue e scrive
la frase che la motiva, che finisce fra i criteri di merito di §9. Ordinare su un giudizio non
riproducibile renderebbe la lista impossibile da spiegare, e §11 promette `explain_match`.

---

## 7. Anatomia (opzionale)

Se il chiamante passa il testo completo, Dovetail calcola la proporzione fra apparato empirico e
resa normativa, come frazioni del word count per sezione.

Il numero non entra nel ranking. Entra come **avvertenza di genere**: se il rapporto supera una
soglia e la shortlist è dominata da riviste di etica, Dovetail lo dice. Caso di riferimento:
il caso, 61% di apparato empirico e resa normativa in quattro frasi, rimbalzato da due riviste di
etica su due.

La segmentazione è euristica e fallisce sui manoscritti mal strutturati. Fallire riportando «non ho
saputo segmentare» è accettabile; indovinare no.

---

## 8. Vincoli

- **`oa_model`, quattro valori e non tre.** v0.1 ne aveva tre e lasciava scoperte 41.521 riviste.
  - `is_in_doaj:true` → `full_oa`
  - `is_oa:false` **con** `apc_usd` → `hybrid` (verificato su *Bioethics*: `is_oa:false`,
    `apc_usd:4550`)
  - `is_oa:false` senza `apc_usd` → `closed_or_unknown` — **non** `closed`: con `apc_usd` nullo sul
    92,7% del corpus, l'assenza di APC non prova che sia chiusa
  - `is_oa:true` **e** `is_in_doaj:false` → **`oa_outside_doaj`**, 41.521 riviste. È il quadrante di
    rischio predatorio (§9)
- **`funder: snsf`** esclude le ibride. **Limite dichiarato:** con solo 8.490 riviste su 206.442
  classificabili come ibride per mancanza di `apc_usd`, il filtro lascia passare molte ibride reali
  sotto etichetta `closed_or_unknown`. Quindi non è un filtro ma un **avviso**: le venue non
  classificabili restano in lista marcate `stato OA non verificabile — controllare a mano prima di
  sottomettere`. Fallire in silenzio nella direzione che il vincolo esiste per prevenire sarebbe il
  guasto peggiore.
- **`max_apc`** — stessa storia: sul topic T10833, `apc_usd:<3500` elimina l'87,8% delle candidate,
  e quasi tutte perché il dato manca, non perché costano. Applicato come marcatore, non come filtro.
- **Riconciliazione APC fra le fonti**, assente in v0.1. Su Venue B, stesso giorno:
  OpenAlex `apc_usd 2290`, DOAJ `apc.max USD 2390`. Regola: **DOAJ vince quando presente**, perché
  è autodichiarato dall'editore e aggiornato al delisting; OpenAlex è il fallback; la shortlist
  mostra entrambi quando divergono di più del 10%.
- `language`, `must_be_indexed_in`, `anvur_class`.
- **`exclude_venues`** — popolata da PaperTrail **attraverso `venue_alias`** (§4), mai per fuzzy
  match al volo.
- **`accepts_unsolicited`** — la corsia dell'opinion non sollecitata alle generaliste risulta chiusa,
  tre desk reject su tre con latenze 6/1/5.

**Sulla latenza.** §0 di v0.1 prometteva «la latenza attesa» e nessuna sorgente la dà. L'unico campo
è DOAJ `publication_time_weeks` (Venue B: 25 settimane), che è il tempo autodichiarato
**fino alla pubblicazione**, non la latenza della **decisione editoriale**, e manca per tutte le
chiuse e ibride. Il dolore di §1 sono desk reject a uno, cinque e sei giorni: un'altra grandezza.
Dovetail mostra `publication_time_weeks` con la sua etichetta esatta, e prende la latenza di
decisione **solo da PaperTrail**, solo dove esiste, dichiarando la numerosità.

---

## 9. Criteri di merito e criteri logistici

Il contributo originale, e viene da un post-mortem: la scelta di Venue B poggiava su
quattro criteri, **tre logistici** (veloce, open access, APC basso) **e uno solo di merito** (genere
adiacente). È caduto quello.

Ogni venue in shortlist mostra i criteri in due colonne:

- **merito** — sovrapposizione di scope, famiglia disciplinare, tipo di articolo compatibile con la
  forma del testo, pubblico raggiunto.
- **logistica** — velocità, APC, OA, conformità al finanziatore, editor conosciuto, indicizzazione.

**Una venue che sta in piedi su meno di due criteri di merito è mostrata in rosso**, per quanto bene
se la cavi sulla logistica.

E: **ampiezza di pubblico non è tenuta dell'argomento.** Un desk reject motivato con «interessa gli
specialisti, non il nostro pubblico ampio» è informazione sul posizionamento, non sul paper.

### Il rischio predatorio, che la griglia da sola aggrava

Una rivista predatoria segna **quattro criteri logistici pieni** (veloce, OA, APC basso, nessun
embargo) e almeno uno di merito, perché il suo profilo di topic è largo per costruzione. La griglia
qui sopra, da sola, **la promuove**.

Quindi `predatory_risk` è un campo di primo livello e un filtro visibile in shortlist, alimentato da:
`oa_model = oa_fuori_doaj`; `works_count` alto con `h_index` basso; assenza da NLM e Scopus;
`host_organization_name` nullo (14,3% delle candidate campionate); DOAJ mancante o revocato.
Nessuno di questi da solo condanna. Insieme, alzano una bandiera che l'utente deve chiudere a mano.

---

## 10. Freschezza

Ogni **campo** porta la propria `verified_at`, non il record.

- Campi da API: rinfrescati in blocco.
- Campi da guidelines: `stale` oltre 180 giorni. Una finalista con campi stale viene riverificata
  durante la consultazione, e la verifica deposita una proposta. La manutenzione è un sottoprodotto
  dell'uso.
- La UI mostra l'età del campo accanto al valore. Una tabella che non dichiara la propria età è
  peggio di nessuna tabella.
- **Assente e scaduto sono stati diversi con lo stesso effetto**, e §6 stadio 4 li tratta insieme.

---

## 11. Superficie MCP

Letture libere, **scritture solo come proposte**, approvazione in UI.

| tool | cosa fa | stato |
|---|---|---|
| `match_venues(title, abstract, word_count, funder, max_apc, discover)` | la lista con criteri e avvertenze | ✅ |
| `explain_match(run_id, venue_id)` | punteggi ai tre livelli, criteri, vincoli, e lo **snapshot** del momento | ✅ |
| `list_runs(limit)` | le consultazioni passate, con i vincoli di ciascuna | ✅ |
| `get_venue(venue_id)` | record completo con `verified_at` **per campo** e la fonte di ogni timbro | ✅ |
| `list_article_types(venue_id)` | tipi e limiti di parole | ✅ (tabella quasi sempre vuota) |
| `search_venues(q, limit)` | ricerca lessicale su nome e ISSN | ✅ |
| `budget_status()` | crediti residui, da guardare prima di `match_venues` | ✅ |
| `list_sources()` | sorgenti con i loro hints | ✅ |
| `list_proposals(status)` | la coda | ✅ |
| `propose_venue` / `propose_update` | depositano in coda, non scrivono | ✅ |
| `venue_history(venue_id)` | cosa è già successo lì, da PaperTrail | ❌ **non implementato**: richiede una chiave PaperTrail lato server, che è una decisione di deploy e non di codice |

**Nessun tool approva.** È la garanzia che rende sicuro puntarci un agente, ed è protetta da un
test che fallisce se qualcuno aggiunge un tool il cui nome contiene *approve*, *delete* o *remove*.
`approve-alias` resta sulla CLI finché non c'è la UI: proporre non è approvare, e la regola
«l'approvazione vive nella UI» non è stata piegata per comodità.

`record_outcome` **non esiste**: gli esiti stanno in PaperTrail, e duplicarli creerebbe due verità.

---

## 12. UI

Quattro schermate: la coda delle proposte (la sola che scrive), il registro delle consultazioni con
i loro esiti, la scheda di una venue con le date di verifica per campo, e lo stato del budget.

---

## 13. Fuori dalla v1

Multi-workspace e ACL. Predizione di accettazione (diciannove venue con uno o due tentativi:
basta per «qui ti hanno già rimbalzato», non per un tasso). Suggerimento di riscrittura
dell'abstract. Conferenze ed editori di libri. Scrittura verso PaperTrail.

---

## 14. Rischi e modi di guasto

1. **La somiglianza di contenuto non è fit di genere.** Il generatore produce candidate plausibili e
   pessime: sul topic di psicologia morale escono `dialectica` (filosofia analitica) e `Games`
   (teoria dei giochi), che respingerebbero una survey a vignette in ventiquattr'ore. **Gli
   embedding non risolvono questo**, perché un saggio concettuale e uno studio empirico sullo stesso
   argomento stanno vicini. È il rischio che lo stadio 5 esiste per coprire, e resta il rischio
   principale del tool fino a quando quello stadio non è in piedi.
2. **Esaurimento del budget OpenAlex.** Non ipotetico: è successo durante lo sviluppo di questa
   spec. Serve chiave, cache, contatore e degradazione dichiarata (§5).
3. **Dato assente contro dato scaduto.** Il primo è maggioritario e in v0.1 non aveva regola. Ora
   §6 stadio 4.
4. **Riviste che OpenAlex non conosce o conosce male.** 11.268 riviste con zero lavori, 36.744 con
   meno di venti, 1.931 di queste dentro DOAJ. Una rivista nuova ha `topics[]` vuoto: prodotto
   scalare zero, **indistinguibile da "fuori scope"**. Il segnale che §2 celebra e il segnale "non
   ho dati" sono lo stesso numero. Serve una terza uscita esplicita, `insufficient profile`.
5. **Abstract corto o generico.** Verificato: lo stesso paper in una frase produce topic disgiunti.
   Guard-rail in §6 stadio 1.
6. **Riviste predatorie.** §9.
7. **OpenAlex resta punto singolo di guasto per l'enumerazione, non più per il giudizio.**
   L'architettura a tre strati attenua molto questo rischio: il giorno che la tassonomia sbaglia un
   topic, il fit non dipende più da quella, perché lo scope lo dà l'embedding sul corpus e il genere
   lo dà l'LLM. Resta che senza OpenAlex non si enumera, e per quello non esiste alternativa gratuita.
   Gli abstract scaricati per gli embedding vanno **conservati**, così un'interruzione dell'API non
   azzera anche i profili di scope già costruiti.
   *Nuovo rischio introdotto dalla v0.3:* gli embedding vanno da qualche parte, e cambiare modello di
   embedding invalida tutti i profili insieme. Il modello e la sua versione si registrano accanto al
   profilo, e un cambio è un reindicizzazione dichiarata, non un aggiornamento silenzioso.
8. **Deriva dei dati fra due consultazioni.** Risolta dallo `venue_snapshot` in §4.
9. **Il tool ottimizza ciò che misura.** Misura scope e vincoli, non qualità dell'argomento né
   ambizione. Se diventa l'unico input alla decisione, spinge verso il fit e via dall'azzardo. Sta
   scritto qui perché resti una scelta consapevole.

---

## 15. Aperto, da chiedere a Spit

- **Creare un account OpenAlex gratuito** e mettere la chiave in configurazione: dieci
  classificazioni al giorno diventano cento. Non registro account per conto tuo.
- ~~Il caso sta su fondi SNSF?~~ **Risolto: sì.** Quindi il vincolo sulle ibride morde davvero, e
  sul paniere della §2 toglie **tre venue su otto** — le tre ibride — prima ancora di guardare lo
  scope. È il caso in cui un vincolo logistico fa più lavoro del punteggio, e vale la pena averlo
  visto: su un paper finanziato SNSF la lista utile si accorcia da sola.
- ~~il caso è su PaperTrail?~~ **Risolto il 27 ago:** sì, in un workspace di gruppo e non in quello personale. La revisione aveva cercato solo in `giovanni-spitale`, che ne contiene
  diciannove: i paper di gruppo stanno in `ite`. **Conseguenza per §4:** `venue_alias` e
  `venue_history` devono interrogare **entrambi** i workspace, e il seed delle venue non può
  fermarsi al vocabolario di `giovanni-spitale`.
- **E una conferma sul campo per §8:** il record p/46 ha `journal: "Venue A"`
  diversa da quella della submission aperta. Il campo `journal` registra la venue passata,
  non il bersaglio. `venue_history` deve leggere le **submission**, mai quel campo.
- Seed delle venue: le diciannove di PaperTrail, o l'insieme più largo generato dai topic dei paper
  già pubblicati?
- Soglia di `stale` a 180 giorni, taglio a dodici venue: ragionevoli?

---

## 16. Fasi successive

- **Fase 1** — schema DB, `venue_alias`, ingestione OpenAlex + DOAJ, matcher stadi 1-2 e stadio 3
  nella versione a topic, guard-rail, seed dalle venue di PaperTrail. Nessuna UI.
- **Fase 1b, prima di fidarsi** — due cose insieme, perché si misurano l'una con l'altra:
  **(a)** costruire i profili di embedding e verificare che battano i topic sul caso il caso e su
  almeno un secondo caso; **(b)** seconda validazione **con almeno un esito positivo** e col paniere
  **generato dal sistema**, non scelto a mano. Finché non passa, la shortlist è un suggerimento e
  l'output lo dichiara.
- **Fase 2** — criteri merito/logistica, `predatory_risk`, vincoli, coda proposte, UI.
- **Fase 3** — MCP, deploy borant, Borant ID.
- **Fase 4** — **stadio 5**: estrazione guidelines con Haiku e giudizio di genere con Sonnet, con
  entrambe le uscite che passano dalla coda di proposte. Poi anatomia e `venue_history`.

**Ordine, e perché.** Lo stadio 5 è il pezzo che avrebbe evitato i desk reject del 2026, e sta in
fondo lo stesso: senza inventario, profili e vincoli non ha finaliste da leggere, e farlo girare su
259 candidate sarebbe la versione lenta e cara della stessa risposta. Se la Fase 1b dicesse che il
punteggio di scope non separa niente, lo stadio 5 va anticipato e il resto del ranking degradato a
filtro — quello è il bivio vero, e si decide con dei numeri, non adesso.

---

## 16c. Cosa ha trovato la Fase 1b (27 ago 2026)

La Fase 1b esiste per rispondere alla metà di domanda che la Fase 0 non poteva toccare: i punteggi
alti corrispondono a venue che hanno detto **sì**? Il metodo è misurare, su paper di Spit già
pubblicati, **in che posizione esce la rivista che li ha davvero presi**.

**La circolarità, dichiarata perché gonfia ogni numero.** Il profilo di una rivista su OpenAlex è
costruito dai lavori che ha pubblicato, **incluso il paper sotto esame**. Un paper pubblicato è
quindi quasi garantito di condividere topic con la propria rivista. Due cose limitano il danno e
nessuna lo elimina: l'effetto vale circa un lavoro su `works_count`, quindi è trascurabile su una
rivista grande e materiale su una piccola, ed è riportato per paper; e la posizione della venue vera
si misura contro candidate che non sono contaminate da niente, quindi una venue vera che *non* si
piazza nonostante il vento in poppa è un risultato negativo pulito. **Un buon esito qui si legge
«non falsificato», mai «validato».**

### I due difetti che ha trovato, entrambi in produzione e non nella validazione

**Il limite di 2000 caratteri di `/text/*`.** L'API rifiuta titolo più abstract oltre i duemila
caratteri, su GET e su POST allo stesso modo. Quattro paper su sette sono falliti lì. L'abstract del
il caso ci stava **per caso**, sui 1200 caratteri: un abstract lungo normale rompe lo stadio 1, e
il difetto era in produzione da sempre. Ora il testo si tronca al confine di parola, mai il titolo, e
il numero di caratteri scartati viaggia nel payload perché la corsa possa registrare che la
classificazione è stata fatta su un testo accorciato. La chiamata è passata a POST: come GET la
query string portava l'abstract intero e sforava anche il limite di 8 KB della URL, che era un
secondo modo di rompersi sopra il primo.

**Lo stadio 2 prendeva il 4,7% delle candidate, e le più grandi.** Per un paper il pool reale è di
**4228 riviste**; ne prendevo **200**, cioè una pagina, e `/sources` ordina per `works_count`
decrescente. Quindi la fetta era «le riviste più enormi» e **ogni specialistica era esclusa per
costruzione**. Si è visto come cinque paper pubblicati su sette la cui rivista vera non entrava
nemmeno fra le candidate. Ora si impagina con cursore fino a 25 pagine, e una pagina costa 1
credito: il costo non è mai stato la ragione per non farlo, era una svista. Il rapporto di
copertura — pool, quante recuperate, se troncato — finisce nella corsa, perché uno sweep limitato
che non dichiara di esserlo si legge come «abbiamo guardato tutto».

**E sotto c'era un limite strutturale che la paginazione non tocca.** `/sources?filter=topics.id:`
restituisce una rivista solo se quel topic sta fra i suoi **primi venticinque**, e quella lista è
troncata. Misurato: l'*International Journal of Public Health* ha **76 lavori** sui tre topic di un
paper e il filtro non la trova, per quante pagine si scorrano. Nessuna rivista che pubblichi sul tuo
argomento *di lato* è raggiungibile per quella strada.

Da qui il **secondo meccanismo**: raggruppare le *opere* per rivista
(`/works?group_by=primary_location.source.id`), che trova chi pubblica sull'argomento e le ordina
per rilevanza al topic invece che per dimensione. Le due liste si uniscono, e i record mancanti si
recuperano in blocco, cento per credito.

**E misurato, non ha funzionato.** Sui sette paper della validazione aggiunge fra le 46 e le 156
candidate e **non cambia un solo esito**: le tre riviste vere che mancavano mancano ancora, e le
posizioni delle altre si spostano di tre.

| | solo paginazione | + gruppi per opera |
|---|---|---|
| Int. J. of Ethics Education | #1 / 2987 | #1 / 3033 |
| JMIR mHealth | #5 / 4882 | #5 / 5038 |
| Neuroethics | #444 / 3681 | #447 / 3810 |
| IJPH, Philosophical Psychology, JMIR Formative | non trovate | non trovate |

Il motivo è che `group_by` ordina per **conteggio assoluto** e si ferma a 200 gruppi, quindi le
riviste che aggiunge sono altre grandi, non le marginali che doveva raggiungere. Sta scritto qui
invece di essere tolto in silenzio, perché un correttivo che non correggerà è un'informazione: la
prossima persona che vede il buco di recall saprà che questa strada è già stata provata.

**Cosa proverei invece, e perché non l'ho fatto ora.** Ordinare i gruppi per *quota* — lavori sul
topic diviso lavori totali della rivista — invece che per conteggio, che è la misura di
specializzazione e farebbe emergere le IJPH. Ma la quota richiede il `works_count` di ogni rivista,
che si conosce solo dopo aver recuperato il record: è un ordinamento che si può fare in scoring e
non in generazione. E resta il dubbio che IJPH non sia raggiungibile in nessun modo, visto che non
emerge nemmeno per topic singolo con filtro d'anno.

**Ma qui la validazione dice una cosa sul metodo, non sul tool.** IJPH non emerge nemmeno per topic
singolo né filtrando per anno: 52 lavori contro centinaia di migliaia. Quel paper è finito lì per
ragioni che il tool **non modella** — rapporti, inviti, velocità, appartenenza istituzionale. Il che
significa che questa validazione **non può separare** «il matcher sbaglia» da «il matcher ottimizza
una cosa diversa da quella che ha guidato la scelta storica». È un risultato negativo sul disegno
della validazione, e va scritto qui perché non venga riscoperto: confrontarsi con le scelte passate
misura la sovrapposizione fra due criteri, non la bontà di uno.

**Una proprietà del metro, emersa due volte.** Il coseno è invariante di scala ma non di forma, e
contro un profilo di testo appuntito premia i profili appuntiti. Si era visto con la venue manuale,
che parte svantaggiata perché nove articoli danno un vettore piatto; si rivede al rovescio ora che
il pool è completo, con la shortlist dominata da riviste **piccole e specialistiche** (*JMIR
Infodemiology*, *European Journal of Health Communication*, *Health & New Media Research*) e le
generaliste grandi che scompaiono. Il metro non misura «quanto è adatta questa rivista», misura
**quanto è concentrata sul tuo argomento**. Sono due cose diverse e finora la spec le ha confuse.

---

## 16b. Cosa ha trovato il primo giro live (27 ago 2026)

Tre reperti dalla prima esecuzione vera del matcher sul caso il caso.

**`type:journal` include telegiornali.** *FOX6 News Milwaukee* è uscita **decima in shortlist con
tre criteri di merito**: 2811 «lavori», h-index 1, nessun editore. Corretto con il flag `is_core`
di OpenAlex, applicato come esclusione **allo stadio 4 e non nella query**, così resta visibile
fra le escluse. Non prende tutto: *Journal of Student Research* è `is_core: true`.

**L'ordinamento per solo subfield era indifendibile.** *Journal of Cognitive Neuroscience* (topic
0.017) usciva sopra *Philosophical Psychology* (topic 0.326), perché il subfield premia quanto una
rivista è specializzata, non quanto aderisce a questo testo. Ora `2 × topic + subfield`, con il peso
dichiarato in configurazione e **non validato**.

**Il matcher non riproduce la scelta reale.** Nella top-12 non c'è la
venue su cui il paper è davvero finito. In cima escono *Philosophical Psychology*, *Journal of
Health Communication* e *Health Communication*, che per aderenza di argomento sono difendibili e
forse migliori. Non è né una conferma né una smentita: è la domanda che la Fase 1b deve chiudere, ed
è il motivo per cui §2 dice che il punteggio di scope non ha validazione. Va anche notato che la
scelta reale fu presa con criteri che il tool ancora non ha — pubblico, genere, e il fatto che due
riviste di etica avessero già detto no.

**Una rivista che OpenAlex non conosce.** Il seed non trova *Future of Science and Ethics*, dove c'è
un paper **accepted** (PaperTrail p/2). È il primo caso concreto di §14.4: su quella venue il
matcher è cieco, e il seed l'ha marcata `not_found` invece di appiccicarla al risultato più
somigliante.

E il seguito, che è peggio e sta nel codice e non nei dati: una venue **dichiarata a mano** — non
esclusa da nessun vincolo, punteggio zero perché non ha profilo — non finiva né in shortlist né
fra le escluse. **Spariva.** `cut` filtrava su `subfield > 0`, buttando via proprio la nota
`insufficient profile` che §14.4 aveva chiesto di introdurre. Il vocabolario per distinguere
«non so» da «fuori scope» esisteva, e la pipeline lo scartava un passo dopo averlo prodotto.

Corretto con un **terzo cestino**: `cut` restituisce anche i non classificabili, che escono in
una lista dichiarata — non ordinati con gli altri, perché un punteggio che non esiste non si
confronta, ma nemmeno nascosti.

### Venue dichiarate a mano

Da qui, `manual.py`, sul modello delle sorgenti di GrantRadar e con la stessa cardinalità: le
riviste che contano e che nessun indice copre sono una decina, non ventimila, e si curano a mano
una volta.

- `add-venue` crea la rivista con i metadati che una persona può leggere dal sito. `is_core` resta
  **`None` e non `False`**: non essere nell'indice curato di OpenAlex non è un giudizio di qualità,
  ed è la differenza fra «non lo so» e «no».
- `profile-venue` costruisce il profilo di scope dagli **articoli realmente pubblicati** dalla
  rivista, classificandoli uno per uno. È la stessa idea degli embedding di §5 — il profilo viene
  dal corpus, non da una tassonomia dichiarata — fatta con lo strumento che c'è già. Costa 100
  crediti per articolo, quindi cinque-dieci è il punto giusto, presi da annate diverse: dieci
  articoli dello stesso monografico descrivono quel numero, non la rivista. `topics_coverage` resta
  `None`, perché lì il campione *è* tutto ciò che si sa e chiamarlo copertura sarebbe fingere una
  misura.

**Qui si scrive diretto e non si propone**, ed è coerente con §11 e non un'eccezione: la coda esiste
per ciò che un *agente* deduce. Qui a scrivere è una persona che sta guardando la rivista, ed è la
stessa che approverebbe. Il timbro dice `manuale`, che è un'affermazione attribuita e datata.

Per le riviste che pubblicano **solo PDF** — cioè quasi tutte quelle che nessun indice copre —
`scripts/pdf_to_articles.py` estrae titolo e abstract dalla prima pagina. Preferisce l'abstract
inglese quando l'articolo è bilingue, e **scarta dichiarando** quando non riesce a isolarne uno,
invece di consegnare un campione inventato.

#### Il caso reale, e il limite che ha rivelato

*Future of Science and Ethics*, profilata da **nove articoli** presi su tre annate (2023, 2024,
2025), passa da invisibile a **posizione 25 su 215**, `generabile: True`, con field 0.7287. Ma non
entra nei primi dodici, e la ragione non è che la rivista non c'entri:

> **Un profilo costruito da un campione non è confrontabile con uno costruito da un corpus.**

Nove articoli producono diciannove topic quasi tutti con `count` 1: un vettore **piatto**. Una
rivista indicizzata ha centinaia di lavori che concentrano la massa su pochi topic: un vettore
**appuntito**. Il coseno è invariante di scala ma non di forma, e contro un profilo di testo
appuntito — tre topic con score 0,99 — premia i vettori appuntiti. La venue curata a mano parte
quindi svantaggiata *per come è stato costruito il suo profilo*, non per quello che pubblica.

Da qui, tre strade, e la scelta va presa con dei numeri in Fase 1b: allargare il campione (venti o
trenta articoli), normalizzare la forma dei due profili prima di confrontarli, oppure classificare
le venue manuali in una **lista a parte** e non nella stessa graduatoria. La terza è la più onesta e
la meno utile; la seconda è quella che gli embedding renderebbero naturale.

Avvertenza sul campione usato: tre dei nove articoli vengono dal numero monografico 2024
sull'intelligenza artificiale, e infatti *Ethics and Social Impacts of AI* esce a 4 occorrenze su 9.
Il profilo descrive anche la mia scelta di articoli, non solo la rivista.

**Reperto laterale con conseguenze fuori dal tool:** la rivista è **fascia A ANVUR per 11/C3 —
Filosofia Morale, dal 2025**, cioè il settore in cui l'ASN *non* ha dato l'abilitazione (11/C2 sì).
Da cui una correzione al modello: `anvur_class` porta ora `settore:fascia` e il confronto è per
appartenenza. Una fascia senza il suo settore non vuol dire niente, e confrontarla per uguaglianza
escluderebbe una rivista valida per più settori.

---

## 17. Storia delle revisioni

### v0.3 — tre strati invece di uno

La v0.2 faceva fare a OpenAlex tre lavori diversi: enumerare, giudicare lo scope e reggere il fit.
Il primo lo fa bene e non ha alternative; il secondo lo fa con venticinque slot troncati di una
tassonomia generale; il terzo non lo fa affatto.

Quindi: **inventario** a OpenAlex, **scope** agli embedding sul corpus recente, **genere e forma** a
un LLM sulle sole finaliste. Il principio in testa al documento — fidarsi di ciò che OpenAlex
contiene, non di ciò che pensa — è la regola da cui discende il resto.

Conseguenze: `corpus_embedding` nel modello dati con modello e versione accanto; lo stadio 3
passa agli embedding tenendo i topic come secondo segnale e diagnostica; nuovo stadio 5 con Haiku
per l'estrazione e Sonnet per il giudizio, entrambi che **propongono e non scrivono**; §14.7
attenuata sul giudizio e riscritta sull'enumerazione, con il nuovo rischio del cambio di modello di
embedding; Fase 1b che misura embedding e ranking insieme.

**Non validato, e dichiarato:** che gli embedding battano i topic. Il budget OpenAlex era esaurito
il giorno in cui è stata scritta questa versione.

### v0.2 — cosa è cambiato da v0.1

Revisione avversariale del 27 ago 2026, verificata a campione in modo indipendente.

**Affermazioni di v0.1 che non reggevano:** `/text/topics` «senza chiave» (è tariffata, 100 crediti);
lo zero di §2 presentato come misura (è aritmetica); «migliore fra le full OA» (le full OA nel
paniere erano due, e una era il desk reject noto); `oa_model` a tre valori (ne mancava uno da 41.521
riviste); `max_apc` come filtro (il campo è nullo sul 92,7%); il campo `publisher` (non esiste);
«~30 candidate per topic» (sono 70/156/33); Venue B come esempio di «field alto, topic
zero» (il suo field è quinto su otto).

**Buchi strutturali chiusi:** `venue_alias`, `text_profile` e `venue_snapshot` persistiti,
`source_id` sulle proposte, taglio della shortlist, guard-rail sugli abstract corti, regola sul dato
assente, spostamento dei vincoli dallo stadio 2 al 4, `predatory_risk`, `list_runs` e
`list_article_types`.

**Una affermazione della revisione che non ho accolto:** che il 500 di `/text/keywords` fosse un
budget esaurito letto male. Nella stessa sequenza `/text/topics` e `/text/concepts` rispondevano
200, quindi il 500 era reale. La distinzione conta per la gestione errori (§5).

---

*Documento vivo. Cosa è dimostrato, alla v0.3: il generatore di candidate ha una validazione
retrodittiva su un caso solo. Il punteggio di scope non ne ha nessuna, né nella versione a topic né
in quella a embedding. Il giudizio di genere non è ancora stato scritto. La Fase 1b esiste per
cambiare questa riga.*
