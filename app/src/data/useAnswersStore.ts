// SPDX-License-Identifier: AGPL-3.0-only
// Additional terms: see NOTICE
// Review-inbox answers persistence: ./api/answers via the family store server
// (merge-by-key on PUT — the echo is adopted so two family members' answers
// propagate to each other). Document logic lives in lib/answers.ts. All
// mutations use functional commits so rapid answer/undo sequences chain
// correctly. No client-side pruning: the server's union-merge would resurrect
// removals anyway; the store caps the doc at MAX_ANSWERS one-off answers instead, and never drops a
// vendor-scope answer (lib/answers.ts capAnswers).
import { useCallback, useMemo, useRef } from "react";
import {
  Answer, AnswersDoc, ANSWERS_LOCAL_KEY, AnswerSet, emptyAnswers,
  makeTxnAnswer, makeVendorAnswer, normalizeAnswers, removeAnswer, restoreAnswer, upsertAnswers,
} from "../lib/answers";
import { clock } from "../lib/clock";
import { canonicalJson, storeUrl, useSyncedDoc } from "./useSyncedDoc";

/** the store keeps its answers oldest first and the client keeps its own order, so "the same answers" is
 *  compared by key: an echo that only reorders them is not adopted (nothing downstream recomputes) */
const answersFingerprint = (d: AnswersDoc) =>
  canonicalJson({ ...d, answers: [...d.answers].sort((a, b) => (a.key < b.key ? -1 : a.key > b.key ? 1 : 0)) });

export interface AnswersApi {
  loading: boolean;
  localOnly: boolean;
  /** the status of a save the store refused (400, 413), until a save gets through */
  refused: number | null;
  doc: AnswersDoc;
  answerTxn: (txnId: string, set: AnswerSet, note?: string) => void;
  /** one commit for N rows (one debounced PUT), the bulk editor's write path — one txn answer per row, so
   *  the store's cap (the newest MAX_ANSWERS one-off answers) sees all of them at once */
  answerTxns: (txnIds: string[], set: AnswerSet, note?: string) => void;
  answerVendor: (vendor: string, set: AnswerSet, note?: string) => void;
  /** undo (Review's toast): puts back the answer this session's last save of `key` replaced — a save merges
   *  into an earlier answer for the same key (lib/answers.ts mergeAnswer), so undoing it must restore that
   *  one, not erase the family's earlier answer — and drops the key when the save was its first.
   *  (Another client's copy can union a drop back — undo is best-effort within the ~6s toast window,
   *  which is how it's exposed.) */
  unanswer: (key: string) => void;
}

export function useAnswersStore(): AnswersApi {
  // no saveState here: nothing shows it, and a field that ticks saving → saved → idle on every save would
  // re-render every useData consumer three times per answer
  const { loading, localOnly, refused, doc, commit } = useSyncedDoc<AnswersDoc>({
    url: storeUrl("answers"),
    localKey: ANSWERS_LOCAL_KEY,
    normalize: normalizeAnswers,
    empty: emptyAnswers,
    adoptEcho: true,
    // the store unions every PUT (merge_answers), so a tab that comes back re-reads the family's answers, and a
    // session whose load never reached the store pushes its own once the store answers
    resyncOnVisible: true,
    union: true,
    fingerprint: answersFingerprint,
  });


  // what each key held before this session's last save of it, for unanswer() (null: it was new). Written
  // inside the commit's functional update, which runs exactly once and synchronously, never during render.
  const priors = useRef(new Map<string, Answer | null>());

  /** write `answers` in ONE commit: each merges into the answer already under its key */
  const write = useCallback((answers: Answer[], now: string) => {
    commit((cur) => {
      for (const a of answers) priors.current.set(a.key, cur.answers.find((x) => x.key === a.key) ?? null);
      return upsertAnswers(cur, answers, now);
    });
  }, [commit]);

  const answerTxn = useCallback((txnId: string, set: AnswerSet, note?: string) => {
    const now = clock.nowISO();
    write([makeTxnAnswer(txnId, set, now, note)], now);
  }, [write]);
  const answerTxns = useCallback((txnIds: string[], set: AnswerSet, note?: string) => {
    const now = clock.nowISO();
    write(txnIds.map((id) => makeTxnAnswer(id, set, now, note)), now);
  }, [write]);
  const answerVendor = useCallback((vendor: string, set: AnswerSet, note?: string) => {
    const now = clock.nowISO();
    write([makeVendorAnswer(vendor, set, now, note)], now);
  }, [write]);
  const unanswer = useCallback((key: string) => commit((cur) => {
    const prior = priors.current.get(key);
    priors.current.delete(key);
    return prior ? restoreAnswer(cur, prior, clock.nowISO()) : removeAnswer(cur, key, clock.nowISO());
  }), [commit]);

  return useMemo(() => ({ loading, localOnly, refused, doc, answerTxn, answerTxns, answerVendor, unanswer }),
    [loading, localOnly, refused, doc, answerTxn, answerTxns, answerVendor, unanswer]);
}
