#!/usr/bin/env node
// The QA browser. Signs in to the dev instance as the QA login (an admin whose
// chats are made up: scripts/dev_qa_account.py), opens a page, runs your steps
// and writes what you shot as one PNG. Open the path it prints with read_file.
// Every request that is not a GET is aborted, so a run changes nothing and any
// number can run at once.
//
//   QA=/root/fuse-dev/scripts/qa/shot.mjs
//   node $QA --find ornith                                        the selector of whatever shows that text
//   node $QA --view all --shot '[data-chat-form]'                 the composer in all three views
//   node $QA --thread rows --open-rows --shot '.chat-step.is-console-call'   one call row of a QA chat
//   node $QA --tree dev,/root/worktrees/NAME --shot '[data-chat-form]'   dev next to a worktree
//   node $QA --page /usage,/admin/users --size 1440xauto          whole pages that are not the chat
//   node $QA --click '[data-chat-input]' --type / --shot          type "/" and shoot what opens
//   node $QA --thread rows --eval 'document.querySelectorAll(".chat-step").length'   measure, no image
//
// Where. A comma list gives one labelled cell per combination, all in one image:
//   --list              print the QA chats and stop
//   --thread TEXT       open the QA chat whose title contains TEXT (any case)
//   --page PATH[,PATH]  open another page: /usage, /settings, /admin/users ...
//                       Default: /chat with no chat open
//   --view V[,V] | all  combined | console | desktop (default). Chat and the
//                       settings pages; sign-in and artifact pages are drawn
//                       one way and shot once
//   --tree DIR[,DIR]    serve /static from this checkout: a worktree's JS and
//                       CSS before they merge. "dev" is the dev tree itself.
//                       Templates and Python always come from the dev instance
//   --size WxH[,WxH]    viewport, default 1440x900. WxAUTO is as tall as the
//                       page needs, so nothing scrolls
//   --touch             a touch screen (no hover, coarse pointer, overlay
//                       scrollbars that take no width), not a mouse;
//                       with --size 390x844 a phone. A phone shows the chat
//                       list over /chat until --thread opens a chat
//   --zoom N            N device pixels per CSS pixel (1 to 4), to read small things
//   --fake NAME[,NAME]  a live state, made by rewriting what the page reads from
//                       the server. Nothing runs:
//                         running  the last reply is still being worked on: status
//                                  line, clocks, a call still running (use with
//                                  --thread "live command", or "live code" for
//                                  a run_code whose calls are still running)
//                         ask      a pending permission prompt
//                         asks     three at once (a command, a subagent's file write,
//                                  a file read outside the project): the 1 of 3 card
//                         skill    a skill's script files waiting to be copied to a
//                                  machine: one made by Fuse, one from GitHub (with
//                                  asks, the other three as well)
//                         error    the last reply failed at the provider
//                         agents   the agents pane (Desktop inspector, Combined
//                                  rail) lists made-up subagents: running, done,
//                                  stopped, failed (use with --thread subagents
//                                  or --thread "live agents")
//                         stats    every chat is a project on the made-up machine
//                                  qa-box, whose stats socket answers in the page
//                                  with a sample a second. --eval "__qaStats.mode
//                                  = 'busy'" (or 'hot') raises the load;
//                                  __qaStats.stop() ends the samples (the card
//                                  goes stale 7 s later)
//                         dictation  the composer has a mic (dev has no speech
//                                  server, so its pages have none) that hears a
//                                  steady made-up voice: --click '[data-chat-mic]'
//                                  opens the live overlay and words land in the
//                                  box. The mic's markup is --tree's template
//   --trace JSON        with --fake running: the reply's running calls become
//                       these (an object, or an array of them), running unless
//                       they say otherwise. A call with "parent": true is made
//                       by the first one, a run_code. E.g. '{"name":"read_file",
//                       "path":"/srv/demo/app.py"}', or '[{"name":"run_code"}]'
//                       for a run_code before its first call
//
// Steps, run in the order given, once per combination, with a real mouse and keys:
//   --find TEXT         print a selector for each visible element whose text,
//                       label or name holds TEXT, and for what it sits in
//                       (its row, its menu): how to learn what to --shot
//   --click CSS         click the first visible match; fails if something
//                       else lies on top of it
//   --hover CSS         move the pointer onto it
//   --type TEXT         type into whatever has focus (click it first)
//   --key KEY           Enter, Escape, Tab, ArrowDown, a, Control+k ...
//   --wait CSS | MS     until a match is visible (5 s at most), or that many ms
//   --open-groups       open every folded work line of a chat
//   --open-rows         open those and every call row, in a group or on its own
//   --eval JS           run page script and print what it returns (a promise is
//                       awaited)
//   --shot [CSS]        one cell: the first visible match, or the viewport. An
//                       element taller than the area it scrolls in (a long
//                       transcript) is shot whole; a box that scrolls its own
//                       content (a menu with a fixed height) is shot as shown
//   --pad N             N px of what is around the element as well (default 0):
//                       after a --shot for that shot, before the first for all
//   With no --shot, the viewport is shot after the last step, unless the run
//   only reads the page (--find, --eval): then no image is made.
//
// Output:
//   --out FILE.png      where the image goes. Default: /tmp/fuse-qa/, which this
//                       tool owns and clears of PNGs older than an hour
//   --toasts            keep toast notices in the shot (hidden by default)
//   --motion            let the page animate (reduced motion is emulated by
//                       default so shots are stable); for checking motion
//   --live              let writes to /chat/* through, so steps can send a
//                       message: --click '[data-chat-input]' --type hi --click
//                       '[data-chat-send]' --wait 20000. A real run on the GPU
//                       server: keep prompts tiny. It can use fetch and web
//                       search, not shell or file tools, and leaves a chat
//                       behind: reseed with scripts/dev_qa_account.py
//   --base URL          the dev instance, default https://127.0.0.1:8088
//
// read_file scales down an image side over 2048 px, so a tall shot is cut into
// parts and a big sheet into pages (-1.png, -2.png). Exit 1 on failure, 2 on a
// bad option; the page's script errors are listed. The browser work is the
// qa-shot skill's core (app/bundled_skills/qa-shot/scripts/shot.mjs); this file
// and fuse-setup.mjs add Fuse's login, views, chats and fakes. Tracked on dev,
// never released to main; the login stays in data/.
import { main } from '../../app/bundled_skills/qa-shot/scripts/shot.mjs';
import setup from './fuse-setup.mjs';

await main(process.argv.slice(2), setup);
