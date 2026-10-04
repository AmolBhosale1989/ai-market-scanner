# Application-authority and fenced-recovery candidate

Validation branch only. Not enabled in scanner/, PR #84, main, Render or production PostgreSQL.

The original 009 draft and original reduced-fixture 18-test module remain byte-identical. The full-schema gate assembles the frozen 25c2c8bf application, migration 009, and this additive 010 candidate. It reruns all 708 existing tests, then 44 full-schema contracts with real PostgreSQL transactions.

The upgraded positive fixture now commits READ -> VIEW -> PRODUCER before publication; full-schema inheritance preserves the original assertions with appropriate fixtures. The diagnostic count test now verifies all three immutable artifacts, rather than the original fixture's single READ. Test 25 directly attempts a READ-parent projection. Test 26 has a valid producer but deliberately omits the physical published dataset, and asserts the distinct database error at COMMIT. Neither negative case can pass from an unrelated exception.

Source deadlines are computed from composite observation references: max bar start per consumed instrument + 5-minute duration + 600 seconds, then min across required views. This candidate supports declared five-minute evidence only; complete daily/calendar and graph instrumentation remain integration work. Hashes do not prove execution by an adversarial database owner.

The dispatcher records CLAIMED and durable SENDING in separate short transactions before an injected, no-retry transport. All mutations check owner/fence/state. Recovery requeues only expired CLAIMED records; expired SENDING becomes terminal DELIVERY_UNKNOWN. A process-death test exits a real child after simulated acceptance, then verifies durable SENDING, recovery quarantine and no second transport call. No Telegram endpoint is called. At-most-once external delivery is not certified.

The authority writer is a candidate adapter exercised against real application tables; it is not wired into the live rotation producer/control-plane publisher. Full runtime-role grants, all producer dependency declarations, published-dataset immutability, semantic alert keys across publications, presentation ranking, and performance/live-overlap validation remain separate gates.
