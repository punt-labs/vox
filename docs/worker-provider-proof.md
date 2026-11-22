# PL-1303 worker proof

Real execution on this feature branch, using installed Claude Code **2.1.323**,
an inherited user configuration, real git worktrees and `tmux -L vox-workers`.
Authentication was passed only in the daemon environment. Full raw output is
retained on the proof VM under `~/work/proof/`; no credential is in these files.

## Start, SessionStart readiness and work product

`vox worker start --task "add a greeting.py that prints hello" --project
/home/ubuntu/work/demo-repo` returned:

```json
"id": "17fe73c5-8e34-40f8-ade8-797cd1375e11",
"session": "vox-worker-17fe73c5-8e34-40f8-ade8-797cd1375e11",
"state": "starting"
```

A subsequent `vox worker list` reported:

```json
"state": "ready",
"last_report": {
  "session_started": true,
  "goal": "add a greeting.py that prints hello",
```

The actual tmux pane first showed `Write(greeting.py)`, then:

```text
● I added greeting.py in the worktree root. It contains one line,
  print("hello"). I haven't run it or committed it.
```

Git status/diff and execution in the worker worktree showed:

```text
## vox-worker/17fe73c5-8e34-40f8-ade8-797cd1375e11
?? greeting.py
```

```diff
@@ -0,0 +1 @@
+print("hello")
```

```text
hello

exit: 0
```

## Daemon crash and reconstruction

The initial run demonstrated that tmux survived `kill -9` of voxd, but exposed
a readiness regression. The failing-first regression test is
`test_ready_survives_restart_until_provider_exits`: it compares the complete
ready record after reconstructing a registry with an empty readiness source,
then verifies that provider death still changes its state to exited.

The corrected real run used worker `72a970bd-4b8f-4b7f-8d2f-d666305c2fb9`.
After a real SessionStart established ready, the proof killed daemon PID 46832:

```text
$ kill -9 46832
$ tmux -L vox-workers ls
vox-worker-72a970bd-4b8f-4b7f-8d2f-d666305c2fb9: 1 windows (created Sun Nov 22 16:03:07 2026)
```

After starting a replacement voxd, `vox worker list` returned the same UUID,
session and worktree, with state `ready` and `session_started: true`. The proof
compared the entire report with the pre-crash persisted report:

```text
PASS: live worker rebuilt as ready with identical persisted report
```

Raw corrected restart output: `~/work/proof/d-rebuilt-fixed.txt`.

After the review fixes, the flight was repeated with worker
`47fb427a-abd4-4622-ab0e-3d8765f1e2e9` and the normal `--enabled-only` context
relay gate active in the inherited user hooks. It reached ready without a
repository enable marker, survived daemon SIGKILL, and rebuilt ready with the
same persisted report. Admission requires a matching live worker record,
session UUID and exact worktree; it does not deposit a repository enable marker
or a permission/settings profile. The same raw output file contains this run.

## Stop and retained work product

After `vox worker stop 17fe73c5-8e34-40f8-ade8-797cd1375e11` returned stopped:

```text
$ tmux -L vox-workers ls
no server running on /tmp/tmux-1000/vox-workers

exit: 1

$ pgrep -af claude

exit: 1

Trust flag after stop: None
Worktree remains: True
```

Git also retained `vox-worker/17fe73c5-8e34-40f8-ade8-797cd1375e11`.
An initial stop race was reproduced before fixing it with
`test_stop_waits_for_hup_resistant_pane`; the final real stop left no Claude
process immediately upon return.

The automated subprocess suite deliberately uses a trivial harness: it must
remain credential-free and runnable without Claude installed. The real Claude
flight above is the complementary authenticated proof, not a mocked hook event.
