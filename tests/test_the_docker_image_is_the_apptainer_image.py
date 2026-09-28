"""The Docker image is built from the Apptainer definition, not beside it.

Services that run FastMDXplora in containers, a hosted GUI among them, need a
Docker image; clusters need the Apptainer one. Two recipes kept in step by hand
would drift, and a study would give a different answer depending on which
image ran it. So `container/docker_from_def.py` writes the Docker build from
`container/fastmdx.def`: the installation and the checks that fail a bad build
are copied from its `%post`, and the image's own test is its `%test`. These
check that nothing is added or lost on the way, and that the workflow builds,
tests and publishes it as it does the Apptainer image.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
DEFINITION = ROOT / "container" / "fastmdx.def"
WORKFLOW = ROOT / ".github" / "workflows" / "container.yml"


@pytest.fixture(scope="module")
def maker():
    spec = importlib.util.spec_from_file_location(
        "docker_from_def", ROOT / "container" / "docker_from_def.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def build(maker, tmp_path: Path) -> Path:
    maker.write(tmp_path)
    return tmp_path


@pytest.fixture(scope="module")
def parts(maker) -> dict[str, str]:
    return maker.sections(DEFINITION.read_text(encoding="utf-8"))


class TestTheInstallationIsTheDefinitions:

    def test_post_is_copied_whole(self, build, parts) -> None:
        """Every package, the CUDA pin and the checks that fail the build."""
        assert (build / "post.sh").read_text(encoding="utf-8").strip() == parts["post"].strip()

    def test_the_image_test_is_the_definitions(self, build, parts) -> None:
        test = (build / "test.sh").read_text(encoding="utf-8")
        assert parts["test"].strip() in test
        assert "AmberToolsToolkitWrapper.is_available()" in test

    def test_the_dockerfile_installs_nothing_of_its_own(self, build) -> None:
        """A package named here would be one the Apptainer image lacks."""
        lines = (build / "Dockerfile").read_text(encoding="utf-8").splitlines()
        body = "\n".join(line for line in lines if not line.startswith("FROM "))
        for word in ("micromamba", "conda install", "pip install", "openmm"):
            assert word not in body

    def test_it_starts_from_the_same_image(self, build, maker, parts) -> None:
        dockerfile = (build / "Dockerfile").read_text(encoding="utf-8")
        assert f"FROM {maker.base_image(parts['header'])}\n" in dockerfile

    def test_the_version_and_cuda_are_chosen_as_for_apptainer(self, build, parts) -> None:
        """%post reads both with defaults; the build passes both in."""
        dockerfile = (build / "Dockerfile").read_text(encoding="utf-8")
        assert "ARG FASTMDX_VERSION\n" in dockerfile and "ARG CUDA_VERSION\n" in dockerfile
        assert "${FASTMDX_VERSION:-" in parts["post"] and "${CUDA_VERSION:-" in parts["post"]

    def test_a_build_that_names_no_version_stops(self, build) -> None:
        """The definition's default is a placeholder the release workflow
        replaces; built by hand without a version, it would install an old
        release and say nothing."""
        dockerfile = (build / "Dockerfile").read_text(encoding="utf-8")
        guard = dockerfile.index('RUN test -n "$FASTMDX_VERSION"')
        assert guard < dockerfile.index("RUN bash -eux /opt/fastmdx/post.sh")

    def test_every_setting_of_the_environment_is_carried(self, build, maker, parts) -> None:
        dockerfile = (build / "Dockerfile").read_text(encoding="utf-8")
        settings = maker.environment(parts["environment"])
        assert ("OPENMM_PLUGIN_DIR", "/opt/conda/lib/plugins") in settings
        for name, value in settings:
            assert f"ENV {name}={value}\n" in dockerfile

    def test_it_runs_what_the_apptainer_image_runs(self, build) -> None:
        assert 'ENTRYPOINT ["fastmdx"]\n' in (build / "Dockerfile").read_text(encoding="utf-8")


class TestWhatAServiceNeeds:

    def test_it_does_not_run_as_root(self, build) -> None:
        dockerfile = (build / "Dockerfile").read_text(encoding="utf-8")
        last_user = [line for line in dockerfile.splitlines() if line.startswith("USER ")][-1]
        assert last_user == "USER $MAMBA_USER"

    def test_the_workspace_is_home_and_owned_by_that_user(self, build) -> None:
        dockerfile = (build / "Dockerfile").read_text(encoding="utf-8")
        assert "ENV HOME=/workspace\n" in dockerfile
        assert 'chown "$MAMBA_USER:$MAMBA_USER" /workspace' in dockerfile
        assert "WORKDIR /workspace\n" in dockerfile

    def test_it_offers_the_guis_port(self, build) -> None:
        assert "EXPOSE 8765\n" in (build / "Dockerfile").read_text(encoding="utf-8")


class TestItRefusesWhatItWouldGetWrong:

    def test_an_environment_line_it_cannot_carry(self, maker) -> None:
        """Apptainer would run it and Docker would silently lose it."""
        with pytest.raises(maker.DefinitionError, match="not understood"):
            maker.environment("    export A=1\n    source /opt/setup.sh\n")

    def test_a_runscript_that_is_not_one_program(self, maker) -> None:
        with pytest.raises(maker.DefinitionError):
            maker.entrypoint("    cd /tmp\n    exec fastmdx \"$@\"\n")

    def test_a_definition_that_does_not_start_from_docker(self, maker) -> None:
        with pytest.raises(maker.DefinitionError, match="Docker image"):
            maker.base_image("Bootstrap: library\nFrom: ubuntu:22.04")

    def test_a_missing_section(self, maker, tmp_path) -> None:
        broken = tmp_path / "broken.def"
        broken.write_text("Bootstrap: docker\nFrom: x:1\n\n%post\n    true\n", encoding="utf-8")
        with pytest.raises(maker.DefinitionError, match="%environment"):
            maker.write(tmp_path / "out", broken)


@pytest.fixture(scope="module")
def job() -> dict:
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))["jobs"]["docker"]


class TestTheWorkflowBuildsTestsAndPublishesIt:

    def test_it_follows_the_apptainer_build_and_its_version(self, job) -> None:
        workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
        assert job["needs"] == "build"
        assert workflow["jobs"]["build"]["outputs"]["version"] == "${{ steps.version.outputs.value }}"
        assert job["env"]["FASTMDX_VERSION"] == "${{ needs.build.outputs.version }}"

    def test_it_is_written_from_the_definition(self, job) -> None:
        steps = " ".join(step.get("run", "") for step in job["steps"])
        assert "python3 container/docker_from_def.py build/docker" in steps
        assert "docker build" in steps and "build/docker" in steps

    def test_it_is_tested_before_it_is_published(self, job) -> None:
        names = [step.get("name") for step in job["steps"]]
        assert names.index("Say what is in it") < names.index("Publish it")
        test = next(s for s in job["steps"] if s.get("name") == "Say what is in it")
        assert "/opt/fastmdx/test.sh" in test["run"]

    def test_only_a_release_is_published(self, job) -> None:
        publish = next(s for s in job["steps"] if s.get("name") == "Publish it")
        assert publish["if"] == ("startsWith(github.ref, 'refs/tags/') || "
                                 "github.event.inputs.release_tag != ''")
        assert "ghcr.io" in publish["run"] and "--password-stdin" in publish["run"]
        assert job["permissions"] == {"contents": "read", "packages": "write"}

    def test_latest_moves_only_on_a_new_tag(self, job) -> None:
        latest = next(s for s in job["steps"] if s.get("name") == "Mark it the latest")
        assert latest["if"] == "startsWith(github.ref, 'refs/tags/')"

    def test_the_cuda_default_is_the_apptainer_images(self, job) -> None:
        assert job["env"]["CUDA_VERSION"] == "${{ github.event.inputs.cuda_version || '12.6' }}"


def test_the_hosting_page_says_where_the_image_is() -> None:
    page = (ROOT / "docs" / "hosting.md").read_text(encoding="utf-8")
    assert "ghcr.io/aai-research-lab/fastmdxplora" in page
    assert "docker_from_def.py" in page
