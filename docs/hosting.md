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

Give each person's GUI its own secret. A secret shared between workspaces
would let one person's GUI be reached with another's.

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
Agent's stored model choice with the person's other files.

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
