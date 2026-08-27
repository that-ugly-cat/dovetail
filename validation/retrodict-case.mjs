#!/usr/bin/env node
/**
 * Retrodictive validation: would the topic profile of a manuscript have
 * separated the venues that turned it down from the one that took it?
 *
 * HONEST ANSWER: it validates STAGE 2 (candidate generation), not stage 3 (the
 * scope score). On the case this was built from, the venues that desk rejected
 * share no topic at all with the text, so their zero is arithmetic rather than
 * measurement, and the per-topic generator would never have produced them. See
 * SPEC.md §2.
 *
 * THE CASE ITSELF IS NOT IN THIS REPOSITORY. It is an unpublished manuscript
 * and the record of which journals turned it down, which is not ours to
 * publish: co-authors did not agree to it and a submission is still open. The
 * script reads `case.local.json`, which is gitignored, and falls back to
 * `case.example.json`, which is a made-up manuscript against real public
 * journal records. The findings in the spec keep the numbers and drop the
 * identities.
 *
 * Cost: /text/topics costs 100 OpenAlex credits (the anonymous daily budget is
 * 1000). The profile is cached on disk: the first run pays for it, later ones
 * do not. Delete .cache-text-profile.json to reclassify.
 *
 * Usage:  node validation/retrodict-case.mjs [--mailto you@example.org]
 */

import { readFileSync, writeFileSync, existsSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = dirname(fileURLToPath(import.meta.url));
const CACHE = join(HERE, '.cache-text-profile.json');

const MAILTO = process.argv.includes('--mailto')
  ? process.argv[process.argv.indexOf('--mailto') + 1]
  : 'ono@borant.eu';

const loadCase = () => {
  const local = join(HERE, 'case.local.json');
  const example = join(HERE, 'case.example.json');
  if (existsSync(local)) {
    console.log('(case: case.local.json — not in the repository)');
    return JSON.parse(readFileSync(local, 'utf8'));
  }
  console.log('(case: case.example.json — a made-up manuscript, real journals)');
  return JSON.parse(readFileSync(example, 'utf8'));
};

const api = async (path) => {
  const sep = path.includes('?') ? '&' : '?';
  const res = await fetch(`https://api.openalex.org${path}${sep}mailto=${MAILTO}`);
  if (res.status === 429) {
    throw new Error(
      'OpenAlex budget exhausted (429). The anonymous budget is $0.10/day and resets at ' +
        'midnight UTC; a free account key raises it to $1/day. See SPEC.md §5.'
    );
  }
  if (!res.ok) throw new Error(`OpenAlex ${res.status} at ${path}`);
  return res.json();
};

const shortId = (url) => url.replace(/^https:\/\/openalex\.org\//, '');

/** Four-valued OA model: v0.1 had three and left 41,521 journals uncovered. */
const oaModel = (s) => {
  if (s.is_in_doaj) return `full OA (APC ${s.apc_usd ?? '?'})`;
  if (s.is_oa) return 'OA OUTSIDE DOAJ (risk)';
  if (s.apc_usd) return `HYBRID (APC ${s.apc_usd})`;
  return 'closed or unknown';
};

const textProfile = (topics) => {
  const t = {}, s = {}, f = {};
  for (const x of topics) {
    t[shortId(x.id)] = (t[shortId(x.id)] ?? 0) + x.score;
    s[x.subfield.display_name] = (s[x.subfield.display_name] ?? 0) + x.score;
    f[x.field.display_name] = (f[x.field.display_name] ?? 0) + x.score;
  }
  return { t, s, f };
};

/**
 * Profile of the journal: share of works per topic.
 * topics[] is truncated to 25 per journal, so on a broad generalist this
 * ignores most of what it publishes. See SPEC.md §6.
 */
const venueProfile = (source) => {
  const topics = source.topics ?? [];
  const total = topics.reduce((a, x) => a + x.count, 0) || 1;
  const t = {}, s = {}, f = {};
  for (const x of topics) {
    const w = x.count / total;
    t[shortId(x.id)] = (t[shortId(x.id)] ?? 0) + w;
    s[x.subfield.display_name] = (s[x.subfield.display_name] ?? 0) + w;
    f[x.field.display_name] = (f[x.field.display_name] ?? 0) + w;
  }
  return { t, s, f };
};

/** Cosine, not dot product: the three levels have to sit on the same scale. */
const cosine = (a, b) => {
  const dot = Object.keys(a).reduce((acc, k) => acc + a[k] * (b[k] ?? 0), 0);
  const na = Math.hypot(...Object.values(a));
  const nb = Math.hypot(...Object.values(b));
  return na && nb ? dot / (na * nb) : 0;
};

const getTextProfile = async (theCase) => {
  if (existsSync(CACHE)) {
    console.log('(text profile read from cache — no credits spent)');
    return JSON.parse(readFileSync(CACHE, 'utf8'));
  }
  const res = await api(
    `/text/topics?title=${encodeURIComponent(theCase.title)}&abstract=${encodeURIComponent(theCase.abstract)}`
  );
  writeFileSync(CACHE, JSON.stringify(res, null, 2));
  console.log('(text profile classified and cached — 100 credits)');
  return res;
};

const main = async () => {
  const theCase = loadCase();
  const classified = await getTextProfile(theCase);
  const textTopicIds = classified.topics.map((x) => shortId(x.id));

  console.log('\nTEXT PROFILE\n');
  for (const x of classified.topics) {
    console.log(
      `  ${x.score.toFixed(3)}  ${shortId(x.id)} ${x.display_name}  [${x.subfield.display_name} / ${x.field.display_name}]`
    );
  }

  const text = textProfile(classified.topics);
  const rows = [];

  for (const { issn, outcome } of theCase.panel) {
    const source = await api(`/sources/issn:${issn}`);
    const venue = venueProfile(source);
    const venueTopicIds = (source.topics ?? []).map((x) => shortId(x.id));
    // Stage 2 filters on topics.id: a venue is reachable if and only if it
    // shares at least one topic with the text. THIS is the result, not the score.
    const reachable = textTopicIds.some((i) => venueTopicIds.includes(i));
    rows.push({
      name: source.display_name,
      reachable,
      topic: cosine(text.t, venue.t),
      subfield: cosine(text.s, venue.s),
      field: cosine(text.f, venue.f),
      oa: oaModel(source),
      outcome,
    });
  }

  rows.sort((a, b) => Number(b.reachable) - Number(a.reachable) || b.subfield - a.subfield);

  console.log('\nCANDIDATE LIST (cosine at three levels)\n');
  console.log(
    '  ' +
      'journal'.padEnd(38) +
      'stage2'.padStart(10) +
      'topic'.padStart(8) +
      'subfld'.padStart(8) +
      'field'.padStart(8) +
      '  OA status'.padEnd(26) +
      'outcome'
  );
  for (const r of rows) {
    console.log(
      '  ' +
        r.name.slice(0, 37).padEnd(38) +
        (r.reachable ? 'reachable' : 'NO').padStart(10) +
        r.topic.toFixed(4).padStart(8) +
        r.subfield.toFixed(4).padStart(8) +
        r.field.toFixed(4).padStart(8) +
        ('  ' + r.oa).padEnd(26) +
        r.outcome
    );
  }

  const rejected = rows.filter((r) => /reject/i.test(r.outcome));
  if (rejected.length) {
    const anyReachable = rejected.filter((r) => r.reachable).length;
    console.log(
      `\n  Are the venues that turned it down reachable at stage 2? ${anyReachable === 0 ? 'NO, none of them.' : `yes, ${anyReachable} of ${rejected.length}.`}`
    );
  }
  console.log(
    '  That validates candidate generation. The scope score stays unvalidated:\n' +
      '  the case contains no positive outcome. See SPEC.md §2.\n'
  );
};

main().catch((e) => {
  console.error('Failed:', e.message);
  process.exit(1);
});
