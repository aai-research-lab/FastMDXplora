# Serving the GUI to other people

The GUI is written for one person on their own machine, and it trusts
loopback: whoever reaches `127.0.0.1` is the person at the keyboard. A service
that runs FastMDXplora for other people cannot work that way. The request
arrives through a proxy under a public name, the person signed in there and
not here, and the machine is not theirs.

`fastmdx gui --hosted` is for that case. It changes four things together,
because any one of them alone is a way in.

| | On your own machine | `--hosted` |
|---|---|---|
| Who is answered | Whoever reaches loopback | Only requests carrying the proxy's secret |
| Which names | `localhost`, `127.0.0.1`, `[::1]` | The names given with `--allowed-host`, and loopback for a health check |
| Which folders | Any folder you can read | `--workspace` and what is inside it, nothing else |
| Paths in answers | As they are on disk | Written as `~/...`, the workspace being `~` |

## Starting it

```bash
export FASTMDX_PROXY_SECRET="$(python -c 'import secrets; print(secrets.token_urlsafe(32))')"
fastmdx gui --hosted --host 0.0.0.0 --port 8765 \
    --workspace /workspace --allowed-host app.example.org
```

It refuses to start without a secret of at least 32 characters, without an
`--allowed-host`, or with the top of the file system as its workspace. The
secret is read from the environment, not a flag, so it is not in the process
list, and it is removed from the environment once read, so the runs the GUI
starts do not inherit it.

## What the proxy must do

1. **Sign the person in** and send only their requests to their GUI.
2. **Add the secret** to every request it forwards, as
   `X-FastMDX-Proxy-Secret`, and **remove** any copy of that header a caller
   sent. A request without the right secret is answered with an empty 403,
   before the server looks at it.
3. **Pass the public name through** as `Host`, or one of the names given with
   `--allowed-host`. A POST must carry an `Origin` that is one of them, over
   `http` or `https`, which a browser tab on the page the GUI served does by
   itself.
4. **Name the person**, if the service shows one, in `X-FastMDX-Account-Name`
   (below), and **remove** any copy of that header a caller sent.

Give each person's GUI its own secret. A secret shared between workspaces
would let one person's GUI be reached with another's.

## A way back to the service

The GUI knows nothing of the service around it: its accounts, its other
pages, signing out. `--account-url` names the service's own page for the
person, as a path on the same site, and the menu at the foot of the sidebar
opens with it as **Your account**:

```bash
fastmdx gui --hosted ... --account-url /account/
```

Only a path on the same site is accepted: one leading slash, plain
characters, no scheme and no query.

## The service's name and the person's

`--product-name` shows the service's name at the top of the sidebar, on the
loading screen and in the window's title, in place of FastMDXplora's, and
`--product-tagline` the line under it. With a name and no line, none is shown
(FastMDXplora's is not the service's). The page citing FastMDXplora and the
links to its documentation stay. A name of at most 40 characters and a line of
at most 80, each on one line.

```bash
fastmdx gui --hosted ... --product-name "Example Lab MD" \
    --product-tagline "Simulations for the Example Lab" \
    --product-logo /srv/brand/logo.png
```

`--product-logo` replaces FastMDXplora's mark, as the tab's icon and over the
folded sidebar, and the AAi Research Lab's logo, the avatar at the foot of the
sidebar where nobody is signed in: a PNG, JPEG,
GIF, WebP, ICO or SVG picture (told by its content), at most 256 KB, read once
when the GUI starts and put into each page it serves. A 128 px PNG is enough.

The person is named at the foot of the sidebar, with their initials, from a
header the proxy adds to each request: `X-FastMDX-Account-Name`, the name as
UTF-8, percent-encoded (`Ad%C3%A9%20Lovelace`). A name changed at the service
shows on the next page load, with nothing restarted. Like the secret's, the
proxy must remove any copy of this header a caller sends; only requests that
carry the secret are read at all. Without the header the foot shows the
product name, with the logo as its avatar.

## Sending a study to the service's compute

A service may run studies on compute of its own, such as a GPU it rents,
from a page of its own. `--runs-url` names that page, and the builder then
offers **Run on a GPU** beside **Run on this machine**, and **Run it on a GPU**
beside **Run on this machine as it is** for a config file:

```bash
fastmdx gui --hosted ... --runs-url /runs
```

**Run on a GPU** saves the builder's config in the workspace, beside the
results folder it will write, as `<folder>.yml` (`<folder>-2.yml` and so on
after an earlier one; a file is never written over), and refuses a results
folder that exists already, as **Run on this machine** does. Both then open the page
with `config` and `output` in its query, as the person sees them
(`~/ub-run.yml`, `~/ub-run`). The page is the service's: the GUI starts
nothing, and the person confirms there. The same rule as `--account-url`
applies to the path.

## The Agent's memory of each person

On a person's own computer the Agent keeps a short memory of them in their
settings folder ([What the Agent remembers of you](agent.md#what-the-agent-remembers-of-you)).
A hosted GUI's settings folder is its operator's, so it keeps none there.
One hosted GUI serves one person's workspace, and the service says where
that person's memory is kept, with one of:

```bash
fastmdx gui --hosted ... --memory-dir /srv/memories/person-7
fastmdx gui --hosted ... --memory-store example-db
```

- `--memory-dir DIR` (or `FASTMDX_MEMORY_DIR`): a folder for this person,
  made if it is not there, holding `agent_memory.md` and the changes beside
  it, as on a person's own computer. Inside the workspace, the person also
  sees the file there.
- `--memory-store NAME` (or `FASTMDX_MEMORY_STORE`): a store an installed
  package offers, such as the service's own database. The package names it
  as an entry point in the group `fastmdxplora.memory_stores`; the entry
  point is a store, or a function given `workspace=` (the one folder this
  GUI serves) that returns one. A store has `where` (what the person is told
  of where it is kept), `learns_at_first` (False for a service), `read()`
  and `write(text)` for the memory as Markdown, `read_changes()` and
  `write_changes(text)` for the changes as JSON, `held()` (a lock while one
  is read and written back, across threads and processes alike: the
  Agent learns from a chat in a thread of its own beside the person's
  Settings) and `set_aside()` (for a memory that is not text, so it is not
  written over, returning what the person is told of where it went);
  `read` and `read_changes` give None where there is none yet, as for a
  person new to the service. A read that fails for a moment raises, and no
  change is made while it does; `set_aside()` is called only for a memory
  that is not text. The factory is not told who the person is: one GUI
  serves one person, so a store knows them as the service started that GUI
  (its workspace, or the store's own settings). The protocol is
  `fastmdxplora.agent.memory.MemoryStore`.

With neither, the Agent keeps no memory and Settings says so; with both,
the GUI does not start. In a hosted GUI the Agent learns from a person's
chats only once they turn that on in Settings: a service reading what its
people write for a memory is each person's to choose. The person is told
it is kept by the service, never a path on the server: a memory that cannot
be read or written is said in a sentence, and the detail goes to the
server's log. The service can read it as it can the person's studies, and
should say so to its people, and removes it when it removes the person.

## One folder is the whole world

Every path a request names is read inside the workspace: the file picker,
opening a study, loading or checking a config, the Agent's attachments, and
the folder a run is written to. A path outside it, whether by `..`, by an
absolute path or by a link that leads out, is refused with
"That is outside your workspace." The picker starts at the workspace and
offers nothing above it. Opening a folder on the server's desktop is refused,
since there is nobody at it.

Paths are shown and read as `~/...`. The person sees their own folder, and the
server's layout, the interpreter's path included, is never in an answer. A
file's own contents, shown in the Files tab or attached to the Agent, are
shown exactly as they are on disk.

**What the GUI does not confine.** A study's own settings can name files, a
structure or a trajectory, and those are read by the run, not by the GUI. In a
hosted service each person's workspace runs in a container of its own, and the
container is the boundary for those. Setting `HOME` to the workspace keeps the
Agent's stored AI model choice with the person's other files.

## The container image

Each release is published as a Docker image as well as the Apptainer image on
its release page:

```bash
docker pull ghcr.io/aai-research-lab/fastmdxplora:2.5.8
```

Both come from one recipe, `container/fastmdx.def`. The Docker build is
written from it by `container/docker_from_def.py`, which copies the
installation and the checks that fail a bad build, so the two images hold the
same software and a study gives the same answer in either. The Docker image
adds what a service needs: it runs as a user that is not root, `/workspace` is
its `HOME` and working folder, and it offers port 8765.

A person's GUI, with their workspace on a volume and the GPU passed through:

```bash
docker run -d --gpus all -p 8765:8765 \
    -v person-42:/workspace \
    -e FASTMDX_PROXY_SECRET="$SECRET_FOR_PERSON_42" \
    ghcr.io/aai-research-lab/fastmdxplora:2.5.8 \
    gui --hosted --host 0.0.0.0 --workspace /workspace \
    --allowed-host app.example.org
```

The image's own test runs with
`docker run --rm --entrypoint bash <image> /opt/fastmdx/test.sh`.

## When a job is interrupted

A rented GPU can be taken back part-way through a run. The job that runs a
study again after that runs `fastmdx resume <study> --json` rather than the
study itself: it finishes a study that had finished nothing, carries on one
that was in production from its last checkpoint, and does nothing to one that
had finished, so the same command is right every time the job restarts.
Checkpoints are written every `checkpoint_interval_steps` (10,000 steps by
default, seconds to minutes of work), which bounds what an interruption costs.
A run sent SIGTERM during production costs less: it steps on to its next frame,
writes a checkpoint there and ends, when that frame is within 20 seconds.
Set `FASTMDX_STOP_GRACE_SECONDS` in the container to a little less than the
platform's own grace period between SIGTERM and SIGKILL.
