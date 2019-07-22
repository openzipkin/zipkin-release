#!/usr/bin/env python3
import json
from datetime import datetime, timedelta, timezone
from typing import Callable, Dict, List, Optional

import click
import pygments
import pygments.formatters
import pygments.lexers
import requests_cache

ISO8601_WITH_MICROSECOND_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


def display_version_details(version: Dict) -> str:
    return pygments.highlight(
        json.dumps(version, sort_keys=True, indent=4, default=str),
        pygments.lexers.JsonLexer(),
        pygments.formatters.TerminalFormatter(),
    ).strip()


class ContextObj:
    def __init__(self, api_base_url: str, api_username: str, api_key: str) -> None:
        self.api_base_url: str = api_base_url
        self.session: requests_cache.CachedSession = requests_cache.CachedSession(
            cache_name="requests_cache",
            backend="sqlite",
            expire_after=timedelta(hours=1),
        )
        self.session.auth = (api_username, api_key)
        self.session.headers.update(
            {"User-Agent": "gh:openzipkin/zipkin-release#bintray-cleanup"}
        )

    def request_json(
        self, verb: str, url: str, object_hook: Optional[Callable[[Dict], Dict]] = None
    ) -> Dict:
        if verb == "DELETE":
            request_color = "red"
        else:
            request_color = "cyan"
        click.secho(f"{verb} {url}", fg=request_color)
        response = self.session.request(verb, url)
        response.raise_for_status()

        json_str = response.content
        click.echo(display_version_details(json.loads(json_str)))
        click.echo()

        if (
            "X-RateLimit-Limit" in response.headers
            and "X-RateLimit-Reamining" in response.headers
        ):
            ratelimit_limit = response.headers["X-RateLimit-Limit"]
            ratelimit_remaining = response.headers["X-RateLimit-Remaining"]
            click.secho(
                f"Remaining API rate-limit: {ratelimit_remaining} / {ratelimit_limit}",
                fg="cyan",
            )

        return json.loads(json_str, object_hook=object_hook)


@click.group()
@click.option("--api-base-url", default="https://api.bintray.com/")
@click.option("--api-username", envvar="BINTRAY_USERNAME", required=True)
@click.option("--api-key", envvar="BINTRAY_API_KEY", required=True)
@click.pass_context
def cli(ctx: click.Context, api_base_url: str, api_username: str, api_key: str):
    if not api_base_url.endswith("/"):
        api_base_url += "/"
    ctx.obj = ContextObj(api_base_url, api_username, api_key)


def enrich_version_data(data: Dict) -> Dict:
    data["created"] = datetime.strptime(
        data["created"], ISO8601_WITH_MICROSECOND_FORMAT
    )
    data["updated"] = datetime.strptime(
        data["updated"], ISO8601_WITH_MICROSECOND_FORMAT
    )
    return data


@cli.command()
@click.pass_obj
def clear_cache(obj: ContextObj) -> None:
    obj.session.cache.clear()
    click.echo("Cleared HTTP response cache")


@cli.command()
@click.argument("subject")
@click.argument("repo")
@click.argument("package")
@click.pass_context
def list_versions(
    ctx: click.Context, subject: str, repo: str, package: str
) -> List[Dict]:
    obj: ContextObj = ctx.obj
    package_data = obj.request_json(
        "GET", f"{obj.api_base_url}packages/{subject}/{repo}/{package}"
    )
    version_names = package_data["versions"]

    versions = []
    for version_name in version_names:
        versions.append(
            obj.request_json(
                "GET",
                f"{obj.api_base_url}/packages/{subject}/{repo}/{package}"
                f"/versions/{version_name}",
                object_hook=enrich_version_data,
            )
        )

    return versions


def display_version_names(versions: List[Dict]) -> str:
    return " ".join(v["name"] for v in versions)


@cli.command()
@click.argument("subject")
@click.argument("repo")
@click.argument("package")
@click.argument("older_than_days", type=int)
@click.pass_context
def list_old_versions(
    ctx: click.Context, subject: str, repo: str, package: str, older_than_days: int
) -> List[Dict]:
    versions = ctx.invoke(list_versions, subject=subject, repo=repo, package=package)
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    old_versions = sorted(
        [version for version in versions if version["created"] < cutoff],
        key=lambda v: v["created"],
    )
    new_versions = sorted(
        [version for version in versions if version["created"] >= cutoff],
        key=lambda v: v["created"],
    )

    older_than_days_display = click.style(str(older_than_days), fg="yellow")
    cutoff_display = click.style(str(cutoff), fg="yellow")
    click.echo(f"Cutoff date {older_than_days_display} days ago: {cutoff_display}")
    click.echo(
        f"Found {click.style(str(len(old_versions)), fg='red')} versions created "
        f"BEFORE {cutoff_display}: {display_version_names(old_versions)}"
    )
    click.echo(
        f"Found {click.style(str(len(new_versions)), fg='green')} versions created "
        f"AFTER {cutoff_display}: {display_version_names(new_versions)}"
    )
    return old_versions


@cli.command()
@click.argument("subject")
@click.argument("repo")
@click.argument("package")
@click.argument("older_than_days", type=int)
@click.option("--dryrun/--no-dryrun", default=True)
@click.option("--limit", default=None, type=int)
@click.option("--yes", default=False, is_flag=True)
@click.pass_context
def delete_old_versions(
    ctx: click.Context,
    subject: str,
    repo: str,
    package: str,
    older_than_days: int,
    dryrun: bool,
    limit: Optional[int],
    yes: bool,
) -> None:
    obj: ContextObj = ctx.obj

    if dryrun:
        dryrun_display = click.style("(DRYRUN) ", fg="cyan")
    else:
        dryrun_display = ""

    old_versions = ctx.invoke(
        list_old_versions,
        subject=subject,
        repo=repo,
        package=package,
        older_than_days=older_than_days,
    )
    click.echo()

    versions_to_delete = old_versions[:limit]
    if not versions_to_delete:
        click.secho("No versions to delete, exiting.", fg="green")
        return
    else:
        click.secho(
            f"{dryrun_display}Selected {len(versions_to_delete)} versions to "
            f"delete: {display_version_names(versions_to_delete)}",
            fg="red",
        )

    deleted_versions = []

    for version in versions_to_delete:
        display_version_name = click.style(
            f"{version['owner']}/{version['repo']}/"
            f"{version['package']}@{version['name']}",
            fg="red",
        )

        click.echo(f"{dryrun_display}Candidate for deletion: {display_version_name}")
        click.echo(display_version_details(version))

        if yes:
            click.secho("Invoked with --yes, skipping confirmation prompt.", fg="cyan")

        if yes or click.confirm(
            f"{dryrun_display}Confirm deletion of {display_version_name}"
        ):
            if dryrun:
                click.secho(
                    f"This is a dry-run, not deleting {display_version_name}", fg="cyan"
                )
            else:
                click.secho(str(datetime.now()), fg="cyan")
                obj.request_json(
                    "DELETE", f"{obj.api_base_url}packages/{subject}/{repo}/{package}/versions/{version['name']}"
                )
            deleted_versions.append(version)

        click.echo(f"Done processing {display_version_name}\n")

    click.echo(
        f"{dryrun_display}Deleted {click.style(str(len(deleted_versions)), fg='red')} "
        f"versions: {display_version_names(deleted_versions)}"
    )

    if not dryrun:
        ctx.invoke(clear_cache)


if __name__ == "__main__":
    cli()
