#!/usr/bin/env python3
import json
from typing import Dict, Optional, Callable, List

import click
import pygments
import pygments.formatters
import pygments.lexers
import requests_cache
from datetime import timedelta, datetime, timezone


ISO8601_WITH_MICROSECOND_FORMAT = "%Y-%m-%dT%H:%M:%S.%f%z"


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

    def get_json(
        self, url: str, object_hook: Optional[Callable[[Dict], Dict]] = None
    ) -> Dict:
        click.secho(f"GET {url}", fg="cyan")
        response = self.session.get(url)
        response.raise_for_status()

        json_str = response.content
        click.echo(
            pygments.highlight(
                json.dumps(json.loads(json_str), sort_keys=True, indent=4),
                pygments.lexers.JsonLexer(),
                pygments.formatters.TerminalFormatter(),
            )
        )

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
    package_data = obj.get_json(
        f"{obj.api_base_url}packages/{subject}/{repo}/{package}"
    )
    version_names = package_data["versions"]

    versions = []
    for version_name in version_names:
        versions.append(obj.get_json(
            f"{obj.api_base_url}/packages/{subject}/{repo}/{package}"
            f"/versions/{version_name}",
            object_hook=enrich_version_data,
        ))

    return versions


@cli.command()
@click.argument("subject")
@click.argument("repo")
@click.argument("package")
@click.argument("older_than_days", type=int)
@click.pass_context
def list_old_versions(
    ctx: click.Context, subject: str, repo: str, package: str, older_than_days: int
) -> Dict[str, Dict]:
    versions = ctx.invoke(list_versions, subject=subject, repo=repo, package=package)
    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)
    old_versions = sorted([version for version in versions if version["created"] < cutoff], key=lambda v: v["created"])
    new_versions = sorted([version for version in versions if version["created"] >= cutoff], key=lambda v: v["created"])
    click.echo(
        f"Found {len(old_versions)} versions created BEFORE {cutoff}: {' '.join(v['name'] for v in old_versions)}"
    )
    click.echo(
        f"Found {len(new_versions)} versions created AFTER {cutoff}: {' '.join(v['name'] for v in new_versions)}"
    )
    return old_versions


if __name__ == "__main__":
    cli()
