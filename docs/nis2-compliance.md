# ReleaseRadar: risposte alle sette domande NIS2 sul software

Revisione 1 · 2026-10-11 · vale per il ramo `main` e per le release successive alla 2026.5

Questo documento risponde alle domande che un cliente soggetto alla direttiva NIS2
(UE 2022/2555, recepita in Italia con il d.lgs. 138/2024) può porre a un fornitore di
software. Ogni affermazione verificabile nel repository è controllata dai test in
`tests/test_nis2_evidence.py`: se il codice cambia e il documento no, la CI fallisce.
Quello che il repository non può dimostrare è indicato con la data in cui è stato
verificato, oppure dichiarato assente.

La NIS2 non prevede una certificazione di prodotto: gli obblighi della direttiva
ricadono sui soggetti, non sugli strumenti che usano. Quello che segue sono le prove
che il fornitore è in grado di dare, non un attestato di conformità.

ReleaseRadar segue repository GitHub (issue, pull request, release) e registra in un
database SQLite locale quello che cambia. Si usa da riga di comando, da una dashboard
web locale (`releaseradar web`) e come server MCP (`releaseradar mcp`).

## 1. Di cosa è fatto il software

- Dipendenze dichiarate in `pyproject.toml`: `typer`, `rich` e `httpx`
  nell'installazione base; il pacchetto `mcp` con l'extra `[mcp]`, che l'immagine
  Docker installa sempre. La risoluzione completa, dipendenze di sviluppo comprese, è
  in `uv.lock`. La CI (workflow Quality) fallisce se `uv.lock` non corrisponde più a
  `pyproject.toml`.
- Ogni release dalla 2026.5 in poi porta in allegato, su GitHub, due SBOM in formato
  CycloneDX 1.5: quella dell'immagine Docker, pacchetti di sistema inclusi, e quella
  dell'ambiente Python del pacchetto installato da sorgente con l'extra `[mcp]`, come
  nell'immagine (vedi la tabella al punto 6). L'ambiente Python è risolto il giorno in cui la SBOM viene
  prodotta, quindi le versioni possono differire da quelle dell'immagine, che ha la
  propria SBOM. La release 2026.5 ha ricevuto questi allegati il 2026-10-11, dopo la
  pubblicazione; le release dalla 2026.1 alla 2026.4 non li hanno.
- Provenienza firmata: i pacchetti su PyPI (wheel e sorgenti) hanno un'attestazione
  PEP 740 firmata con Sigstore dal workflow di pubblicazione `publish.yml`; le immagini
  su Docker Hub (`maksimtech/releaseradar`) hanno un'attestazione di provenienza SLSA
  generata da BuildKit al momento del push. Entrambe verificate il 2026-10-11 su tutte
  le versioni pubblicate, dalla 2026.1 alla 2026.5.
- Licenze: il workflow License Check fallisce se una dipendenza dell'installazione
  base dichiara una licenza GPL, AGPL o LGPL. Le dipendenze che l'extra `[mcp]`
  aggiunge non sono controllate da quel workflow; il 2026-10-11 lo stesso controllo,
  eseguito a mano su un ambiente con l'extra, non ne ha trovata nessuna con quelle
  licenze.

## 2. Chi può vedere cosa, dentro l'applicazione

ReleaseRadar gira sul sistema di chi lo esegue. Non ha account, sessioni né ruoli,
quindi non implementa controlli di accesso: chi può leggere il database e la variabile
`GITHUB_TOKEN` lo decide il sistema su cui gira, ed è responsabilità dell'operatore.
Una matrice ruoli-permessi non esiste perché non ci sono ruoli.

Oltre alla riga di comando ha due interfacce:

- **La dashboard** (`releaseradar web`) è un server HTTP della libreria standard di
  Python che ascolta solo su `127.0.0.1`, sulla porta 8765 o su quella indicata con
  `--port`. L'indirizzo non si può cambiare: non esiste un'opzione `--host`. Serve
  HTTP in chiaro e non ha autenticazione: qualunque utente o processo della stessa
  macchina che raggiunge quella porta può leggere i repository seguiti e gli eventi
  registrati, e chiedere una sincronizzazione con `POST /api/sync`, che usa il token
  di chi ha avviato la dashboard. Il server non controlla le intestazioni `Host` e
  `Origin`: serve la richiesta qualunque sia la pagina da cui arriva, e l'unico filtro
  verso le pagine web aperte nel browser è quello che il browser stesso applica.
- **Il server MCP** (`releaseradar mcp`) comunica su stdio, cioè attraverso
  l'input e l'output standard del processo che lo avvia, e non apre porte di rete.
  Può usarlo solo il programma che lo ha avviato (il client MCP), con i permessi
  dell'utente che lo esegue. Quel client può chiamare tutti gli strumenti, compreso
  `untrack_repo`, che cancella i dati di un repository, e quelli che contattano GitHub
  con il token.

L'immagine Docker gira come utente non privilegiato `releaseradar`, UID 1000. Il
Dockerfile non dichiara porte (`EXPOSE`) e tiene il database nel volume
`/home/releaseradar/.releaseradar`. L'immagine contiene anche il comando `web`, ma dentro
un container il server ascolta su `127.0.0.1` della rete del container; l'uso della
dashboard da Docker non è documentato e non è stato verificato.

## 3. Se succede qualcosa, si può ricostruire chi ha fatto cosa

ReleaseRadar non tiene un registro delle operazioni: non annota chi ha lanciato un
comando, chi ha aggiunto o tolto un repository, né le richieste ricevute dalla
dashboard, il cui server non scrive log di accesso. Risultati ed errori vanno su
stdout e stderr, e la loro conservazione è a carico di chi lo esegue. La tabella
`events` del database registra quello che è cambiato sui repository seguiti, con l'ora
in cui una sincronizzazione lo ha visto: è una cronologia di GitHub, non delle azioni
di chi usa ReleaseRadar. Non contiene telemetria e non invia nulla al fornitore.

ReleaseRadar contatta un solo servizio esterno:

<!-- hosts:start -->
| Host | Quando | Cosa riceve |
| --- | --- | --- |
| `api.github.com` | `track`; `sync`; `web`, all'avvio, poi ogni 15 minuti (o con l'intervallo di `--every`) e a ogni *Sync now*; `mcp`, con gli strumenti `track_repo`, `sync_repo` e `sync_all` | il nome `owner/repo` di ogni repository seguito, nel percorso della richiesta; dalla seconda sincronizzazione, nella richiesta delle issue, l'ora di quella precedente come filtro (`since`); il token in `GITHUB_TOKEN`, se impostato, nell'intestazione `Authorization` |
<!-- hosts:end -->

- Le pagine successive di un elenco sono chieste all'indirizzo che api.github.com
  indica nell'intestazione `Link` della propria risposta, con le stesse intestazioni,
  token compreso. Il client non segue i redirect.
- Le richieste portano lo User-Agent predefinito della libreria httpx
  (`python-httpx/<versione>`), non uno proprio di ReleaseRadar.
- ReleaseRadar non scrive il token su disco: lo legge dalla variabile d'ambiente
  `GITHUB_TOKEN`, lo tiene in memoria e non lo salva nel database; la dashboard non lo
  mostra. Se l'utente lo scrive in un file di configurazione, come il `.mcp.json`
  dell'esempio nel README, quel file va protetto come qualunque segreto.
- ReleaseRadar non distingue tra repository pubblici e privati: se il token dà accesso
  a un repository privato e lo si segue, il database conterrà titoli, link e note di
  rilascio di quel repository.
- Gli strumenti MCP restituiscono i loro risultati (nomi dei repository, titoli,
  eventi) al client che ha avviato il server. Se quel client li passa a un modello
  linguistico ospitato da terzi, lo decide la configurazione del client, non
  ReleaseRadar.
- La dashboard apre la propria pagina nel browser predefinito, salvo `--no-browser`. La
  pagina non carica risorse da altri siti; i link a GitHub che mostra si aprono solo se
  l'utente li clicca.
- `releaseradar notify --json` stampa gli eventi su stdout, per lo script a cui
  l'utente decide di passarli.

<!-- local:start -->
Da riga di comando `untrack`, `list`, `status`, `events` e `notify`, e tra gli
strumenti MCP `untrack_repo`, `list_repos`, `repo_status` e `recent_events`, leggono o
modificano solo il database locale e non contattano nessuno.
<!-- local:end -->

Su disco ReleaseRadar scrive solo il database SQLite, in `~/.releaseradar/state.db`
oppure nel percorso indicato dalla variabile `RELEASERADAR_DB`; nell'immagine Docker,
nel volume `/home/releaseradar/.releaseradar`. Contiene:

- `repos`: i repository seguiti, con la data in cui sono stati aggiunti e quella
  dell'ultima sincronizzazione;
- `issues` e `pull_requests`: numero, titolo, stato, link e date di ognuna, e per le
  pull request i numeri delle issue che dichiarano di chiudere;
- `releases`: tag, nome, link, data di pubblicazione, se è una pre-release e il testo
  delle note di rilascio;
- `events`: le transizioni viste (tipo, riferimento, titolo o nome, link, ora, e se
  sono già state notificate).

Non salva gli autori di issue, pull request e release; titoli e note di rilascio sono
salvati così come GitHub li restituisce. Il file non è cifrato e ReleaseRadar non ne
restringe i permessi. `untrack` cancella dal database le righe di un repository; per
eliminare ogni dato si cancella il file.

## 4. Come arrivano gli aggiornamenti, e in quanto tempo

La politica completa è in `SECURITY.md`: fonti di monitoraggio, tempi di risposta,
notifica ai clienti, procedura di rollback. In sintesi:

- le correzioni escono con la release successiva, che può arrivare fino a un mese dopo
  la precedente; per una vulnerabilità con CVSS pari o superiore a 9.0 il cliente che
  ha indicato un contatto di sicurezza viene avvisato entro 48 ore dalla presa in
  carico, anche se la correzione non è ancora pronta;
- le vulnerabilità accettate, con motivazione e data di revisione, sono in
  `SECURITY-EXCEPTIONS.toml`;
- ogni versione ha un tag Docker dedicato (es. `maksimtech/releaseradar:v2026.5`) che
  le ricostruzioni settimanali di `latest` non sovrascrivono, e un digest SHA256
  immutabile; il rollback consiste nel tornare a quel tag o a quel digest;
- il workflow Docker è programmato per ricostruire `latest` ogni lunedì dall'ultimo
  tag, per incorporare le patch di sicurezza di Debian; al 2026-10-11 quella
  ricostruzione non è ancora mai stata eseguita.

Contatto per le segnalazioni: <security@maksimtech.com>.

## 5. Il codice viene controllato prima di andare in produzione

Analisi automatiche, ciascuna con il proprio workflow in `.github/workflows`:

<!-- scanners:start -->
| Strumento | Cosa controlla | Quando |
| --- | --- | --- |
| `CodeQL` | analisi statica del codice | ogni pull request, ogni push su `main`, ogni settimana |
| `Bandit` | analisi statica di sicurezza Python | ogni pull request, ogni push su `main`, ogni settimana |
| `Trivy` | vulnerabilità note nei file del repository | ogni pull request, ogni push su `main`, ogni settimana |
| `Snyk` | vulnerabilità note nelle dipendenze dell'installazione base e nell'immagine; analisi statica del codice | ogni pull request, ogni push su `main` |
| `SonarCloud` | qualità, duplicazioni e copertura | ogni pull request, ogni push su `main` |
| `Docker Scout` | vulnerabilità note nell'immagine | ogni push su `main`, ogni build dell'immagine, ogni settimana |
| `Security Posture` | raccoglie gli avvisi aperti sul ramo `main` pubblicati dagli altri e fallisce se un avviso alto o critico non ha un'eccezione motivata | ogni pull request, ogni push su `main`, ogni settimana |
| `mutmut` | mutation testing: verifica che i test si accorgano delle modifiche al codice | ogni settimana |
<!-- scanners:end -->

Snyk e SonarCloud saltano l'analisi sulle pull request che non ricevono i segreti del
repository (da fork o da Dependabot) e lo segnalano nel log. Security Posture, se le
analisi dello stesso commit non finiscono entro venti minuti o se non può leggere gli
avvisi (per esempio su una pull request da fork), non controlla nulla e lo segnala nel
log invece di fallire; e su una pull request controlla gli avvisi di `main`, non quelli
che la pull request aggiunge, che GitHub mostra nei controlli di code scanning della
pull request. A questi si aggiungono i test automatici (pytest, con copertura inviata a
Codecov), ruff e mypy nel workflow Quality.

Le regole del ramo `main`, lette dalle impostazioni del repository il 2026-10-11,
vietano la cancellazione e la riscrittura della storia, richiedono una storia lineare,
una pull request con zero approvazioni richieste e due controlli superati: Tests e
Quality. L'account del manutentore (`maksimtech`) può scavalcare queste regole in
qualunque momento, e lo fa a ogni release: `release.sh` invia direttamente su `main`,
senza pull request, il commit che aggiorna il numero di versione, ed è così che sono
arrivati su `main` quelli dalla 2026.2 alla 2026.5. Security Posture e
CodeQL girano su ogni pull request ma non sono tra i controlli obbligatori.

Sul repository, alla stessa data, sono attivi la secret scanning di GitHub e la push
protection; gli avvisi Dependabot sono disattivati.

ReleaseRadar ha un manutentore unico. Non c'è nessuna revisione obbligatoria da parte di
una seconda persona: le pull request vengono unite dal manutentore stesso. È un limite
noto di un progetto con una sola persona.

## 6. Qualcuno dall'esterno ha provato a entrare

Non è stato eseguito un penetration test. Esiste una scansione automatica continua
delle vulnerabilità note, descritta al punto 5, ma non è un penetration test: non ha un
ambito concordato, non è condotta da un tester indipendente e non produce un rapporto
con vulnerabilità trovate e correzioni datate.

Ogni release dalla 2026.5 in poi porta in allegato i risultati datati di quella
scansione, insieme alle SBOM:

<!-- assets:start -->
| File allegato alla release | Contenuto |
| --- | --- |
| `sbom-image-<versione>.cdx.json` | SBOM CycloneDX 1.5 dell'immagine Docker della versione |
| `sbom-source-<versione>.cdx.json` | SBOM CycloneDX 1.5 dell'ambiente Python del pacchetto con l'extra `[mcp]` |
| `trivy-image-<versione>-<data>.json` | vulnerabilità note nell'immagine, scansione Trivy |
| `trivy-fs-<versione>-<data>.json` | vulnerabilità note nei file del repository al tag, scansione Trivy |
| `EVIDENCE-<versione>.md` | cosa sono questi file, quando e da quale commit e digest dell'immagine sono stati prodotti |
<!-- assets:end -->

Il rapporto `trivy-fs` allegato alla 2026.5 è vuoto: a quel tag il repository non aveva
un lockfile, e Trivy non ha trovato nessun file da analizzare. Un rapporto vuoto non
indica l'assenza di vulnerabilità, e in quel caso la nota `EVIDENCE` lo dice. Da questa
revisione il repository contiene `uv.lock`, che Trivy analizza: provato il 2026-10-11
con la stessa versione usata dal workflow, la 0.70.0.

## 7. E se il fornitore sparisce

Il codice sorgente è pubblico su <https://github.com/maksimtech/releaseradar> con
licenza MIT: il cliente può conservarne una copia e, per la licenza, usarla, modificarla
e farla mantenere da altri senza chiedere nulla al fornitore. Le versioni pubblicate
sono su PyPI e su Docker Hub, da cui il cliente può scaricarle e conservarle; quanto
restano disponibili lì dipende da quei servizi, non da questo documento.

I dati di ReleaseRadar stanno sull'infrastruttura di chi lo esegue, mai presso il
fornitore: sono il database descritto al punto 3. Non c'è un comando di esportazione
dedicato, e non serve per portarli via: il file è in formato SQLite 3, aperto e
documentato, non cifrato, e si apre con qualunque strumento che legga SQLite; il modulo
`sqlite3` della libreria standard di Python ne produce un dump SQL completo con
`iterdump()`. Gli eventi non ancora notificati si ottengono anche in JSON, senza
segnarli come notificati, con `releaseradar notify --json --dry-run`.

Una clausola contrattuale su questo punto va concordata caso per caso e fatta rivedere
da un legale.
