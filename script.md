Here's the updated script with corrected tool descriptions and a live demo section worked in:

  ---
  Evergreen MCP Server — Presentation Script (3-5 min with live demo)

  ---
  [OPENING — The Problem] (~30 sec)

  How many times have you been deep in a coding session, pushed a patch, and then had to context-switch out of your editor to go check the Evergreen CI dashboard? You click through the waterfall, find the red task, open the logs, scroll through thousands of lines trying to find the actual error. It breaks your flow. It's tedious. And it happens multiple times a day.

  What if your AI assistant could just tell you what went wrong?

  ---
  [WHAT IT IS] (~30 sec)

  That's what the Evergreen MCP Server does. It's a Model Context Protocol server that gives AI assistants — like Claude — direct access to Evergreen's CI/CD platform. Instead of navigating dashboards yourself, you ask your AI: "Why is my patch failing?" and it fetches the data, identifies the failures, and points you to the right logs. All without leaving your editor.

  The server exposes five core tools. You can list your recent patches and their CI status. You can drill into a failing patch to get all the failed tasks with their build variants, failure types, and timeout info. You can pull task-level log entries filtered by severity and error keywords. You can get test result metadata — test names, statuses, durations, and links to the detailed
  logs. And it auto-detects which Evergreen project you're working on based on your workspace and patch history, so there's no manual configuration.

  ---
  [LIVE DEMO] (~2-3 min)

  Let me show you what this looks like in practice.

  [Switch to editor with AI assistant]

  I'm going to ask the AI to check my CI status.

  [Type: "Check my CI status"]

  You can see it called list_user_recent_patches_evergreen and pulled back my recent patches — descriptions, statuses, timestamps. And right here, we can see this patch is showing as failed.

  Now let's dig into that.

  [Type: "What's failing in that patch?"]

  It called get_patch_failed_jobs_evergreen with the patch ID automatically. We get back the failed tasks — task names, build variants, and the failure details. We can see whether it was a timeout, which command failed, and how many tests broke. The AI is already summarizing what it sees.

  Let's go one level deeper.

  [Type: "Show me the test failures for that task"]

  Now it called get_task_test_results_evergreen. This gives us the metadata — the specific test file names that failed, their exit codes, durations, and URLs to the detailed logs in Parsley. The AI can identify exactly which tests broke and give me direct links to inspect the full output.

  So in three conversational turns, we went from "what's my CI status" all the way to specific failing tests with log links — without ever opening the Evergreen dashboard.

  ---
  [ARCHITECTURE — Quick Hits] (~30 sec)

  A few things worth noting under the hood. The server talks to Evergreen through its GraphQL API, so we can fetch exactly the data we need with server-side filtering. Authentication is handled through OIDC with automatic token refresh and cross-process file locking, so multiple MCP instances don't collide. And the server ships with embedded skill documents — debugging workflows and
  Evergreen domain knowledge — that the AI can read at runtime to improve its own troubleshooting strategy.

  ---
  [CLOSING] (~15 sec)

  The Evergreen MCP Server makes CI/CD debugging conversational. It keeps you in your flow state, reduces context-switching, and surfaces the right information fast. We're actively developing it — including upcoming support for fetching full test log content directly. Contributions and feedback are welcome.

  Thank you.

  ---
  Demo prep checklist:
  - Have the MCP server running and connected to your AI assistant beforehand
  - Have a recent patch with failures ready (or know one exists)
  - Test the full flow once before the presentation so there are no auth surprises
  - Have the Evergreen dashboard open in a background tab as a fallback



An AI model is isolated, they only know what they are trained on. They cant see your databse, code or jira without manual copy pasting or custom built plugins for every single app.
the solution is a open standard by anthropic that replaces custom integrations witha  universal connector. think of it like a ucb type c. the cable can accept anything from display, to power to information and it all gets plugged into the same port

but why use a MCP when all your LLM needs is terminal access. it can write the commands to do the same thing

well think of it like a human interacting with a rest api. they need to write the code to connect to it, they need to wrap it to make it safe and extensible. or they can just use a sdk to make it easier to interact with the API.

its the same with LLM's. we use mcp as the easy interface to work with api's/tools