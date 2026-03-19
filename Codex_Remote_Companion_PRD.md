# Codex Remote Companion PRD

## 1. Project Summary

Codex Remote Companion is a lightweight bridge that lets a user interact with a local Codex CLI session from a mobile device. The first target interface is Telegram. A later phase may add a small web UI.

The project is meant to solve a simple problem: Codex is useful on a laptop or desktop, but it is much less convenient when the user steps away from the computer. The goal is to keep the existing strength of Codex, including local repository access and session continuity, while adding a thin remote communication layer.

This system is not trying to rebuild a coding agent from scratch. It uses Codex as the execution engine and adds a remote control surface on top.

## 2. Motivation

The user already has a working local coding workflow with Codex, but that workflow has several practical limits.

First, Codex is tied to the local terminal. If the user leaves the desk, there is no simple way to continue a task, ask a follow-up question, or check progress.

Second, many coding tasks are naturally multi-step and session-based. The user does not want each mobile message to start a brand new task. The user wants to continue the latest session in the same repository.

Third, there is a strong desire to keep the system simple. The user does not want to immediately build a full product with a custom frontend, a cloud backend, and a complex authentication stack. The user wants a direct path to a working prototype.

This leads to the central design choice of the project:

Use Codex for execution, session history, and repository-aware coding, and add a thin remote bridge for communication.

## 3. Product Goals

### 3.1 Primary Goals

The first version should support the following:

- Continue the latest Codex session from a phone.
- Start a new Codex session in a chosen repository.
- Support multiple repositories.
- Return useful execution summaries.
- Keep the architecture simple enough that a single user can run it on one local machine.

### 3.2 Secondary Goals

After the first version works, the system should be able to grow toward:

- Better status reporting
- Better result summaries
- Streaming progress updates
- Approval or confirmation before risky actions
- A simple web dashboard

### 3.3 Non-Goals for Version 1

Version 1 should not try to do the following:

- Rebuild Codex from the OpenAI API
- Support many users
- Provide a polished consumer-grade interface
- Offer cloud-hosted execution
- Solve all security and sandboxing issues in the first pass

## 4. Why Build on Codex Instead of Starting from the OpenAI API

It is possible to build a remote coding agent from the OpenAI API directly. In that approach, the developer would need to design the full agent loop, file tools, command execution, memory, repository context handling, patch generation, test running, and result interpretation.

That path gives maximum control, but it also means rebuilding many parts that Codex already provides.

For this project, the better first step is to build on top of Codex because the main problem is remote access, not agent design. Codex already handles the hard local coding workflow. The bridge only needs to route messages and preserve the connection between a mobile conversation and the latest local session.

The OpenAI API can still be useful later for optional layers such as summarization, routing, or intelligent formatting of results.

## 5. Communication Interface Choice

### 5.1 Telegram

Telegram is the recommended first interface because it is easy to integrate and gives immediate mobile access.

Advantages include:

- No frontend work
- Built-in mobile app
- Simple bot API
- Natural push notifications
- Easy two-way communication

Limitations include:

- Long outputs are awkward
- Diff viewing is poor
- Custom controls are limited

### 5.2 Web UI

A web UI is a reasonable later option. It could support richer views such as logs, structured history, diffs, approval buttons, and repository browsing.

Advantages include:

- Full control over the interface
- Better display for long-running tasks
- Easier path toward a real product

Limitations include:

- Requires frontend development
- Requires backend API design
- Requires authentication
- Requires push or polling for updates
- Higher development cost

### 5.3 Recommended Rollout

The recommended order is:

1. Build a local core bridge.
2. Connect it to Telegram.
3. Add security and workflow improvements.
4. Consider a web UI after the core behavior is stable.

## 6. Core User Stories

### Story 1: Continue Latest Session

As a user, I want to send a plain text message from my phone and have the system continue the latest Codex session in the current repository, so that I can keep working without starting over.

### Story 2: Start a New Session

As a user, I want to explicitly start a new Codex session in a repository, so that I can separate unrelated tasks.

### Story 3: Switch Repositories

As a user, I want to switch the active repository for my chat, so that later messages go to the right codebase.

### Story 4: Check Status

As a user, I want to see which repository is active and whether a task is running, so that I know where my message will go.

### Story 5: Receive a Summary

As a user, I want to receive a short result summary after Codex finishes, so that I can understand the outcome from my phone.

## 7. Functional Requirements

### 7.1 Repository Management

The system shall support a configured list of repositories.

Each repository entry should include:

- A short project name
- An absolute local path
- Optional metadata such as branch or notes

The user shall be able to switch the current project from Telegram.

### 7.2 Session Continuity

The system shall support continuing the latest Codex session for the active repository.

The system shall also support starting a new session.

The bridge should treat the repository as the main context boundary.

### 7.3 Message Routing

Each chat should be mapped to one active repository.

Incoming messages should be routed according to:

- chat identifier
- current project
- requested mode such as new or continue

### 7.4 Task Execution

The bridge shall invoke Codex CLI as a subprocess.

The first version should support at least two actions:

- Run a new task
- Continue the latest task

### 7.5 Result Handling

The bridge shall collect:

- return code
- standard output
- standard error
- start time
- end time

The bridge shall format these into a compact summary suitable for mobile delivery.

### 7.6 Basic Commands

The first version should support the following commands:

- `/project <name>` to switch repository
- `/new <prompt>` to start a new session
- `/continue <prompt>` to continue the latest session
- `/last` to show recent session information
- `/status` to show active project and running state
- `/help` to show available commands

Plain text without a command should be treated as continue latest session.

## 8. Non-Functional Requirements

The bridge should be lightweight and easy to run on a local machine.

The codebase should be understandable by one developer.

The system should tolerate restarts without losing all state.

The first version should favor simplicity over perfect generality.

The system should not require a public cloud deployment.

## 9. High-Level Architecture

The system has five main layers.

### 9.1 Mobile Interface Layer

This is the user-facing communication interface. In version 1, this is Telegram.

### 9.2 Bridge Daemon Layer

This is a small local service that receives messages, interprets commands, routes work, and sends responses back to the mobile client.

### 9.3 Session and Routing Layer

This layer stores the mapping between chat identifiers and active repositories. It also tracks lightweight execution state.

### 9.4 Codex Adapter Layer

This layer hides the details of how Codex CLI is invoked. It turns bridge requests into subprocess calls.

### 9.5 Local Execution Environment

This is the user’s actual machine, local repositories, local shell environment, and installed tools.

## 10. Logical Data Model

A minimal data model is enough for version 1.

### 10.1 Project

A project represents a local repository.

Fields:

- name
- repo_path

### 10.2 ChatState

A chat state binds a Telegram chat to an active project.

Fields:

- chat_id
- project_name
- repo_path
- last_active_at

### 10.3 TaskRun

A task run records a Codex execution attempt.

Fields:

- task_id
- chat_id
- project_name
- prompt
- mode
- status
- started_at
- ended_at
- summary
- stdout_tail
- stderr_tail

## 11. Core Execution Flow

### 11.1 Continue Latest Session

1. The user sends a plain text message or `/continue <prompt>`.
2. The bridge loads the chat state.
3. The bridge resolves the active repository path.
4. The bridge calls the Codex adapter in continue mode.
5. Codex runs inside the repository.
6. The bridge captures output and formats a summary.
7. The summary is sent back to Telegram.

### 11.2 Start New Session

1. The user sends `/new <prompt>`.
2. The bridge loads the chat state.
3. The bridge resolves the active repository path.
4. The bridge calls the Codex adapter in new mode.
5. Codex starts a new task.
6. The bridge captures output and formats a summary.
7. The summary is sent back to Telegram.

### 11.3 Switch Project

1. The user sends `/project <name>`.
2. The bridge validates the project name.
3. The chat state is updated.
4. A confirmation message is returned.

## 12. Pseudocode

### 12.1 Bridge Main Loop

```python
def main():
    load_config()
    load_state_store()

    while True:
        updates = telegram_client.get_updates()
        for update in updates:
            try:
                handle_update(update)
            except Exception as exc:
                safe_send_error(update.chat_id, str(exc))
```

### 12.2 Update Handling

```python
def handle_update(update):
    chat_id = update.chat_id
    text = update.text.strip()

    if not text:
        return

    if text.startswith("/project "):
        project_name = text.split(" ", 1)[1].strip()
        switch_project(chat_id, project_name)
        telegram_client.send(chat_id, f"Active project set to {project_name}")
        return

    if text == "/status":
        state = state_store.get(chat_id)
        telegram_client.send(chat_id, render_status(state))
        return

    if text == "/last":
        state = state_store.get(chat_id)
        info = task_store.get_last_for_project(state.project_name)
        telegram_client.send(chat_id, render_last(info))
        return

    if text.startswith("/new "):
        prompt = text.split(" ", 1)[1].strip()
        run_task(chat_id, prompt, mode="new")
        return

    if text.startswith("/continue "):
        prompt = text.split(" ", 1)[1].strip()
        run_task(chat_id, prompt, mode="continue")
        return

    if text == "/help":
        telegram_client.send(chat_id, HELP_TEXT)
        return

    run_task(chat_id, text, mode="continue")
```

### 12.3 Running a Task

```python
def run_task(chat_id, prompt, mode):
    state = state_store.get(chat_id)
    if state is None:
        raise ValueError("No active project. Use /project first.")

    repo_path = state.repo_path
    telegram_client.send(chat_id, f"Running in {state.project_name}...")

    started_at = now()
    result = codex_adapter.run(repo_path=repo_path, prompt=prompt, mode=mode)
    ended_at = now()

    task = TaskRun(
        task_id=make_task_id(),
        chat_id=chat_id,
        project_name=state.project_name,
        prompt=prompt,
        mode=mode,
        status="done" if result.ok else "failed",
        started_at=started_at,
        ended_at=ended_at,
        summary=result.summary,
        stdout_tail=result.stdout_tail,
        stderr_tail=result.stderr_tail,
    )
    task_store.save(task)

    telegram_client.send(chat_id, render_result(task))
```

### 12.4 Codex Adapter

```python
import subprocess

class CodexResult:
    def __init__(self, ok, stdout, stderr, summary):
        self.ok = ok
        self.stdout = stdout
        self.stderr = stderr
        self.summary = summary
        self.stdout_tail = stdout[-3000:]
        self.stderr_tail = stderr[-1500:]


class CodexAdapter:
    def run(self, repo_path, prompt, mode="continue"):
        if mode == "new":
            cmd = ["codex", "exec", prompt]
        elif mode == "continue":
            cmd = ["codex", "exec", "resume", "--last", prompt]
        else:
            raise ValueError(f"Unsupported mode: {mode}")

        proc = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
        )

        summary = self.make_summary(proc.returncode, proc.stdout, proc.stderr)
        return CodexResult(
            ok=(proc.returncode == 0),
            stdout=proc.stdout,
            stderr=proc.stderr,
            summary=summary,
        )

    def make_summary(self, returncode, stdout, stderr):
        if returncode == 0:
            tail = stdout.strip()[-1200:]
            if tail:
                return f"Completed successfully.\n\n{tail}"
            return "Completed successfully."
        else:
            err = stderr.strip()[-1200:]
            if err:
                return f"Failed.\n\n{err}"
            return "Failed with no stderr output."
```

## 13. Telegram Command Design

The command set should stay intentionally small.

`/project <name>` sets the active repository for the chat.

`/new <prompt>` starts a new Codex task in the active repository.

`/continue <prompt>` continues the latest Codex session in the active repository.

`/last` shows the latest known task information for that project.

`/status` shows the active project and whether a task is running.

`/help` shows usage.

Any plain text message is interpreted as continue.

This is important because it makes the mobile experience natural. The user can simply type a follow-up request without remembering a command every time.

## 14. State Storage

Version 1 can use very simple local persistence.

Two files are enough:

- `config.json` for repository definitions
- `state.json` for chat bindings and last-known state

A later version may switch to SQLite.

### Example `config.json`

```json
{
  "projects": [
    {
      "name": "privsyn",
      "repo_path": "/Users/you/code/privsyn"
    },
    {
      "name": "course",
      "repo_path": "/Users/you/code/data-privacy-course"
    }
  ]
}
```

### Example `state.json`

```json
{
  "chats": {
    "123456789": {
      "project_name": "privsyn",
      "repo_path": "/Users/you/code/privsyn",
      "last_active_at": 1760000000.0
    }
  }
}
```

## 15. Error Handling

The first version should handle the following cleanly:

- Missing project selection
- Unknown project name
- Codex command failure
- Empty user input
- Telegram API errors
- Local process exceptions

The user should receive a short and readable error message.

## 16. Security Considerations

Version 1 is for personal use, but even then some care is needed.

At minimum:

- Restrict the bot to the user’s own Telegram chat
- Do not expose the bridge to arbitrary users
- Validate active project names against a local allowlist
- Never allow arbitrary repo paths from chat input

Later phases should add:

- Approval before risky commands
- Better audit logs
- Better separation of message handling and execution

## 17. Observability

Version 1 should keep simple logs.

Log items should include:

- timestamp
- chat_id
- command type
- project
- success or failure
- task duration

These logs can go to a plain text file.

## 18. Roadmap

### Phase 1: Local Core

Build a local CLI bridge first.

This phase should prove:

- repository routing
- new task execution
- continue latest session
- result summarization

### Phase 2: Telegram Integration

Add Telegram long polling and command parsing.

This phase should prove:

- mobile control
- result delivery
- chat-to-project binding

### Phase 3: Reliability Improvements

Add:

- better logging
- queueing
- timeout handling
- improved summaries

### Phase 4: Safer Workflows

Add:

- confirmations
- optional approval steps
- clearer audit trail

### Phase 5: Optional Web UI

If Telegram becomes limiting, build a small web interface with:

- session list
- task history
- richer logs
- nicer summaries

## 19. Future API-Based Extensions

Once the Codex bridge is working, the OpenAI API can be added in a targeted way without replacing Codex.

Useful API-based additions may include:

- better task summaries
- natural language routing
- structured extraction of changed files or test results
- adaptive formatting for mobile

This hybrid design is stronger than fully replacing Codex at the start.

## 20. Acceptance Criteria for MVP

The MVP is successful if all of the following are true:

- The user can choose a project from Telegram.
- The user can start a new Codex task from Telegram.
- The user can send a normal follow-up message and continue the latest session.
- The system returns a readable completion or failure summary.
- All of this runs on one local machine with simple local configuration.

## 21. Recommended First Implementation Plan

The fastest path is:

1. Build a local command-line wrapper for new and continue.
2. Add JSON or file-based state storage.
3. Add Telegram bot polling.
4. Add basic commands.
5. Improve summaries only after the loop works.

The core idea is to keep version 1 small, understandable, and real.
