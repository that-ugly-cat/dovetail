# Dovetail — User Guide

Dovetail helps you decide where to send a paper. You give it a title, an abstract and, if you have it, the reference list; it gives back lists of candidate journals, each with the reasons that hold it up. It suggests and explains. The decision stays yours.

[TOC]

---

## 1. The process in one page

1. **You paste the manuscript**: title, abstract, optionally the reference list, a word count and your constraints.
2. **Dovetail reads what the paper is about.** OpenAlex classifies the title and abstract into topics, and you are shown them.
3. **It looks for journals in two ways.** By subject: journals that publish on those topics. By your references: journals whose recent papers cite the works you cite.
4. **It weighs each journal.** Reasons of merit are kept apart from logistics. Your constraints mark or exclude. A fact that is missing never excludes.
5. **You read the lists.** They can be reordered. If you want, a model checks whether each journal publishes your *kind* of paper.

A consultation takes one to two minutes. It is visible only to you.

## 2. Getting in

The front page is public and shows nothing about anyone's work. Everything else is behind the sign-in: Borant ID where the instance uses it, a password where it runs on its own. There is no sign-up form. Ask whoever runs the instance for access.

There are two roles. The role decides who may change what everybody shares. It does not decide who may start a consultation: that takes your own OpenAlex key, for members and admins alike.

| | member | admin |
|---|---|---|
| Browse the journals and the review queue | yes | yes |
| Start consultations, with your own OpenAlex key | yes | yes |
| Add a journal by hand, or describe one from its articles | no | yes |
| Approve or reject proposals in the review queue | no | yes |
| Manage users | no | yes |

## 3. Before your first consultation: your keys

Open **Settings**. There are two keys, both optional, both stored encrypted and never shown again.

### OpenAlex key — needed to start consultations

A consultation is paid for in OpenAlex credits, and **each person pays for their own with their own key**. There is no budget shared between users: without a key you can browse the journals and read your consultations, and you cannot start one. A key is free and comes with 10,000 credits a day, enough for dozens of consultations:

1. Make an account at [openalex.org/signup](https://openalex.org/signup) and confirm your email address.
2. Copy the key from **Settings → API key** on openalex.org.
3. Paste it in Dovetail's Settings and save.

Dovetail tries the key against OpenAlex before keeping it, so a mistyped or never-activated key is refused on the spot. The «?» beside the field repeats these steps.

### Anthropic key — only for the two steps that use a model

You do **not** need it to run a consultation. It pays for two optional things: checking whether each journal publishes your kind of paper (section 6), and reading a journal's author guidelines into article types (section 8). Both are charged to your own Anthropic account.

## 4. Starting a consultation

**New consultation** asks for:

- **Title and abstract.** The abstract needs at least 60 words; below that it cannot be classified reliably and Dovetail declines rather than guess. OpenAlex reads at most 2,000 characters including the title, about 300 words; anything beyond is left out, and the consultation tells you so.
- **Reference list** (optional, strongly recommended). Paste the bibliography in any format. Only the DOIs in it are read, up to 150, and at least 5 are needed. References without a DOI — books, mostly — are not seen.
- **Constraints** (optional). A word count, checked against each journal's limits where they are on record. A funder: `snsf` excludes hybrid journals, which the SNSF does not pay for. The highest fee you can pay.
- **Journals that publish in** a language. English is chosen when the form opens; pick another, or «any language». A journal is kept when at least a fifth of its articles are in that language, so a bilingual journal stays. Dovetail reads the language from the journal's own articles the first time it meets it, and remembers it. A journal whose language could not be found is marked for you to check, never removed.
- **Only journals on the Leiden core list.** Unticked when the form opens. The «?» beside it says what the list is. In short: ticked, you get a shorter list with fewer surprises, and lose nearly every journal not published in English, many national journals and a few well-known ones. Being on the list is not a mark of quality.
- **Search OpenAlex for new journals.** Leave it ticked unless you want a quick, almost free look at the journals Dovetail already knows.
- **Look up the candidates in DOAJ.** Free. It adds licence, fees and declared time to publication.

The form shows what the consultation will cost before you press the button, and recomputes it as you paste the references. You are taken to the consultation's page straight away; it fills in as the work goes on, and closing the page does not stop it.

### Why the reference list matters

The abstract is classified by its *subject*. A paper that argues something **about** a technology or a disease is therefore matched to the journals of that technology or disease, not to the journals where the argument belongs. Your references are a second, independent way in: they point to the conversation the paper is part of.

## 5. Reading a consultation

### The two cards at the top

**How your abstract was classified** shows the topics OpenAlex read, with a confidence from 0 to 1. Open *Read the topics in full* to see each one whole. If these look wrong, the list by subject will be wrong too: rewrite the abstract so that it says what the paper argues, and run it again.

**What was searched** says how many journals were looked at, how many of your references were recognised, and how many journals they led to.

### The lists

They are kept apart on purpose. They were found in different ways, and a journal's place means something only inside its own list.

| List | What is in it | Ordered by |
|---|---|---|
| **By subject** | Journals whose published work is closest to the topics of your abstract | Subject match |
| **By your references** | Journals whose recent papers cite the works you cite, reached through at least 5% of your references | Share of the journal's recent papers that cite them |
| **From the smaller groups of your references** | The same, read inside each minority field of your bibliography, up to three journals each | The same |
| **Excluded by your constraints** | Shown only when fewer than three journals passed, each with the constraint that removed it | — |
| **Could not be assessed** | Journals Dovetail knows exist and not what they publish | — |

A journal that both ways lead to stays in the list by subject and says so among its reasons. That is the strongest signal Dovetail can give.

The **smaller groups** matter when most of your references are about the paper's subject and a few are about its argument. Counted together, the few are outvoted. Read one group at a time, they show where the argument is being discussed.

### One journal

- **Subject match** is one number from 0 to 1: twice the topic score plus the subfield score, scaled. It is what the list by subject is ordered by.
- **Topic, subfield, field** are how close the journal is to your abstract at each level of OpenAlex's classification, narrowest first. A high field score with a topic score of zero means the right discipline and the wrong subject.
- **Cites your refs** (‰) is, of every 1,000 works the journal published in recent years, how many cite one of your references.
- **Merit** lists what makes the journal a good home for the argument. **Logistics** lists cost, access and speed. A journal with fewer than two reasons of merit is marked **thin on merit**, however well it does on logistics.
- **No topic in common** means the journal publishes on none of the topics read in your abstract. It was found another way.
- Lines starting with «!» are things to check: an unknown publisher, open access outside DOAJ, a fee that could not be verified.

### Reordering

**Order each list by** sorts inside each list: by subject match, by any of the three scores, by citations, by number of references, by reasons of merit, by fee, or by name. The number in front of a journal is its place in the consultation's own order and stays with it. Values that are missing go last.

### How not to read it

- **It is not a ranking.** A place in a list says nothing about the journal in general.
- **The scores measure concentration, not suitability.** They favour narrow journals over broad ones that would also take the paper.
- **The lists by reference describe your bibliography, not your argument.**
- **Nothing here has been validated against real outcomes.** Use it to find and compare candidates, not to decide for you.

## 6. Checking the kind of paper

An empirical study and a conceptual essay on the same subject belong in different journals, and no score sees the difference. On a finished consultation, **Check N journals** has a model read each candidate's most recent titles and say whether it publishes work of the same kind as yours.

- It needs your Anthropic key as well as your OpenAlex key.
- The cost is shown beside the button: about a cent a journal.
- It takes a few minutes. Keep the page open.
- Each journal gets a line: *publishes this kind of paper* or *a different kind*, with a sentence saying why. **It never changes the order of the lists.** You can then sort by it.

## 7. Journals

**Journals** lists every journal a consultation has looked up, plus the ones added by hand. It grows with use and is not a census. Journals are shared by everyone on the instance.

A journal's page shows its access model and fee, what it publishes, its article types and word limits where someone has read the guidelines, and your own consultations it appeared in. Dovetail says what a journal publishes and what it costs; it does not judge how good or how trustworthy a journal is, so check a journal you do not know before you submit to it.

Two things an admin can do:

- **Add a journal by hand**, for one that OpenAlex does not list. Leave «unknown» wherever you have not checked: answering «no» without looking would exclude it for no reason.
- **Describe a journal from its articles.** A journal with no known subjects cannot be compared with a manuscript. Paste five to ten of its articles, from different years, and Dovetail takes its subjects from them. It costs 100 credits an article, from the admin's own key.

## 8. Article types, word limits and the review queue

No database carries a journal's article types and word limits. They come from its author guidelines, one journal at a time, and reach its record only through the **review queue**: an assistant or a script proposes, a person approves.

A word limit never travels without **what it counts**. 8,000 words including references and 9,000 excluding them give opposite answers for the same manuscript. A limit that does not say what it counts is not written.

Once a journal has its article types, a consultation with a word count tells you which format fits, or that the manuscript is over every limit on record.

## 9. What is yours and what is shared

| Yours alone | Shared by everyone |
|---|---|
| Your consultations: title, abstract, references, lists, verdicts | Journals and everything known about them |
| Your OpenAlex and Anthropic keys | The review queue |
| Your OpenAlex budget | |

Nobody else can open your consultations. That includes admins, who see how many each person has run and what they spent, and nothing of what they are about. A consultation that is not yours answers «not found», exactly like one that does not exist.

You can **delete** a consultation from the bottom of its page. It is removed for good, with all its lists. The journals it found stay.

This separation is between users of the application. Whoever runs the server can read its database.

## 10. What it costs

| What | OpenAlex credits |
|---|---|
| Classifying the title and abstract | 100 |
| Searching for journals by subject | up to about 40 |
| A reference list | 1 per DOI, plus up to about 20 |
| Looking up the language of journals not met before | 1 per journal, up to 60; close to nothing once the journals around your subject are known |
| Checking the kind of paper | 10 per journal whose recent titles are not on record yet |
| Describing a journal from its articles | 100 per article |

All of these come out of your own key: a free OpenAlex key gives 10,000 credits a day, and nobody else's use touches yours. The budget resets at midnight UTC. The model steps are billed in dollars to your Anthropic account: about a cent per journal checked, a fraction of a cent per page of guidelines.

## 11. Using it from an AI assistant

Dovetail has an MCP endpoint at `/mcp`. An assistant with your key can search journals, start a consultation with your reference list, explain why a journal came out where it did, and run the check on the kind of paper. With an admin's key it can also file proposals into the review queue. It works on your behalf: it sees your consultations and no one else's, and spends your keys.

No tool can approve a proposal. Approval is a button in the web app, pressed by a person.

Ask whoever runs the instance for an MCP key.

## 12. When something goes wrong

- **«No list was produced».** The abstract was too short or too generic to classify. Give it more of the paper's actual argument.
- **The list by subject is all about the wrong field.** Look at how the abstract was classified. Then add the reference list, and read the lists by reference.
- **Few DOIs were found.** A bibliography of books has none. The lists by reference need at least five.
- **There is no «New consultation» button.** You have no OpenAlex key stored. Add one in Settings.
- **«Not enough budget».** Your key's credits for today are used up. Wait for midnight UTC, or untick the search for new journals.
- **Your Anthropic key needs a workspace id.** It is a key that works across several workspaces. Add the id in Settings, or use a key that belongs to one workspace.
- **A journal you expected is missing.** Search for it under Journals. If it is not there, no consultation has come across it yet; an admin can add it by hand.
