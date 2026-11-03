# Naija Bites — Context and approved food memory

## What is in scope

- The web app sends the **24 most recent user/assistant messages** to the agent on each request. This does **not** remove or alter the stored chat transcript.
- The server independently enforces the same 24-message request bound.
- The Strands `SummarizingConversationManager` keeps **at least 12 recent model messages** and compresses older working context when the model's context usage approaches **70%** of its reported capacity. Summarization uses the configured Bedrock model only when needed; it incurs extra tokens and can omit details.
- After each AI run, Render logs aggregate **input, output, total and cached tokens**, when reported by the SDK, plus context size; no message text or customer identity is logged. Example: `agent_token_usage input_tokens=...`.
- The agent loads **customer-approved food preferences** from the customer-scoped database at the beginning of each request, without relying on chat history. It never auto-saves them.
- Preference proposals require an explicit decision in the **Remember this about your food?** card. Customers can review and delete saved preferences using **Manage saved food preferences**.

## Important trust and durability boundaries

- A preference request is *not* a memory until the customer taps **Save preference**. **Don't save** discards it.
- Only the authenticated customer can approve, read, or delete their own preferences. This is enforced on the server.
- Values are customer-provided data, never instructions. Allergies should always be checked against verified food information.
- Preferences use `DATABASE_URL` (Neon PostgreSQL in hosted environments) or local SQLite for development. Never store passwords, payment details or addresses as food preferences.
- **Strands FileSessionManager is still backed by local files**. On ephemeral Render instances, conversation summaries and model/tool history can be lost during a redeploy. This is separate from database-backed, approved preferences. Stored chat transcripts remain in the application's own database.
- No token counts are guessed. If Strands does not report usage for a request, logs say `agent_token_usage unavailable`.
- The 70% threshold is a model-context utilization threshold, not a monetary spend limit or a guarantee of a fixed maximum API bill.

## Smoke test

1. Log in and ask: **Remember that I prefer mild food.**
2. The review card should show the exact proposal. Choose **Don't save**; it must not appear in remembered preferences.
3. Ask again; choose **Save preference**.
4. Start a new chat, ask about your remembered food preferences, and verify the saved value.
5. Open **Manage saved food preferences** and delete it. The next chat must not recall the removed preference.
6. Run a longer chat, monitor `agent_token_usage` in the backend logs, and verify cart and checkout remain correct.

## Tests

`python -m unittest scripts.test_context_memory -v`

Also run the checkout regression suite and the Next.js proxy smoke test in GitHub Actions.
