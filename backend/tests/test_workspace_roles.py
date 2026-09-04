"""Workspace roles: a rank, not a bag of flags.

The cases worth pinning are the ones where a permission model usually goes
wrong: an unknown capability failing open, a new default locking out every
existing member, and a rank comparison that accidentally compares strings.
"""
import secrets

import aiosqlite
import pytest
import pytest_asyncio

from backend import coordination as coord
from backend import workspace_roles as wsr
from backend.db import get_db


@pytest_asyncio.fixture(scope="module", autouse=True)
async def schema(client):
    await coord.init_coordination_db()
    await wsr.init_workspace_roles_db()
    await wsr.seed_builtin_templates()


@pytest_asyncio.fixture
async def workspace(client, fresh_agent):
    """A guild with one workspace channel, and its founder."""
    founder = await fresh_agent()
    slug = f"ws-{secrets.token_hex(5)}"
    async with get_db() as db:
        db.row_factory = aiosqlite.Row
        cur = await db.execute("SELECT id FROM agents WHERE name=?", (founder["name"],))
        founder_id = dict(await cur.fetchone())["id"]
        cur = await db.execute(
            """INSERT INTO guilds (slug, name, bio, founder_id, founder_name, guild_api_key)
               VALUES (?,?,?,?,?,?)""",
            (slug, "Role Guild", "", founder_id, founder["name"], secrets.token_hex(16)),
        )
        guild_id = cur.lastrowid
        await db.execute(
            "INSERT INTO guild_members (guild_id, agent_id, agent_name, role) VALUES (?,?,?,'founder')",
            (guild_id, founder_id, founder["name"]),
        )
        cur = await db.execute(
            """INSERT INTO guild_channels (guild_id, slug, name, channel_kind, visibility, flow_mode)
               VALUES (?, 'code', 'Code', 'workspace', 'members', 'open')""",
            (guild_id,),
        )
        channel_id = cur.lastrowid
        await db.commit()
    return {"guild_id": guild_id, "slug": slug,
            "channel": {"id": channel_id, "slug": "code", "channel_kind": "workspace"}}


@pytest_asyncio.fixture
async def principal_maker(fresh_agent):
    async def _make():
        agent = await fresh_agent()
        async with get_db() as db:
            db.row_factory = aiosqlite.Row
            cur = await db.execute("SELECT id FROM agents WHERE name=?", (agent["name"],))
            agent_id = dict(await cur.fetchone())["id"]
        return await coord.get_or_create_agent_principal(agent_id)
    return _make


# ── the rank ─────────────────────────────────────────────────────────────────

def test_roles_are_ordered_least_to_most():
    assert wsr.RANK["observer"] < wsr.RANK["contributor"] < wsr.RANK["operator"]
    assert wsr.RANK["operator"] < wsr.RANK["maintainer"] < wsr.RANK["lead"]


def test_a_capability_is_granted_by_rank_not_by_name():
    """'observer' sorts after 'lead' alphabetically. Comparing the strings
    would grant observers everything, which is the failure this ordering
    exists to prevent."""
    assert not wsr.allows("observer", "push_branch")
    assert wsr.allows("lead", "push_branch")


def test_an_unknown_capability_fails_closed():
    """A typo in a permission check must deny, not permit."""
    assert not wsr.allows("lead", "delete_the_universe")


def test_a_principal_with_no_role_is_below_observer():
    assert wsr.rank_of(None) < wsr.RANK["observer"]
    assert not wsr.allows(None, "read")


def test_every_capability_names_a_real_role():
    for capability, needed in wsr.CAPABILITIES.items():
        assert needed in wsr.RANK, f"{capability} needs unknown role {needed!r}"


def test_higher_roles_hold_everything_lower_roles_hold():
    """Monotonicity. Without it, a promotion could take something away."""
    for lower, higher in zip(wsr.ROLES, wsr.ROLES[1:]):
        assert set(wsr.capabilities_of(lower)) <= set(wsr.capabilities_of(higher))


def test_the_error_says_what_would_have_been_enough():
    with pytest.raises(wsr.RoleError) as excinfo:
        wsr.require("contributor", "bind_repo")
    assert "maintainer" in str(excinfo.value)
    assert "contributor" in str(excinfo.value)


# ── effective role ───────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_guild_member_with_no_row_can_still_work(client, workspace, principal_maker):
    """The default must not be an exclusion: introducing this table cannot
    lock every existing guild out of its own workspaces."""
    member = await principal_maker()
    role = await wsr.effective_role(workspace["channel"], member, "member")
    assert role == wsr.DEFAULT_ROLE
    assert wsr.allows(role, "claim")
    assert not wsr.allows(role, "push_branch")


@pytest.mark.asyncio
async def test_a_founder_can_bind_the_repository_without_being_granted_a_seat(
    client, workspace, principal_maker
):
    """Otherwise creating a workspace locks its creator out of setting it up."""
    founder = await principal_maker()
    role = await wsr.effective_role(workspace["channel"], founder, "founder")
    assert wsr.allows(role, "bind_repo")


@pytest.mark.asyncio
async def test_an_explicit_observer_row_is_a_real_demotion(client, workspace, principal_maker):
    """Below the default, so the row is doing work in both directions."""
    member = await principal_maker()
    await wsr.set_role(channel_id=workspace["channel"]["id"], principal_id=member["id"],
                       role="observer")
    role = await wsr.effective_role(workspace["channel"], member, "member")
    assert role == "observer"
    assert not wsr.allows(role, "post")


@pytest.mark.asyncio
async def test_an_explicit_row_beats_the_guild_role_in_both_directions(
    client, workspace, principal_maker
):
    admin = await principal_maker()
    await wsr.set_role(channel_id=workspace["channel"]["id"], principal_id=admin["id"],
                       role="observer")
    assert await wsr.effective_role(workspace["channel"], admin, "admin") == "observer"


@pytest.mark.asyncio
async def test_a_non_member_holds_nothing(client, workspace, principal_maker):
    outsider = await principal_maker()
    assert await wsr.effective_role(workspace["channel"], outsider, None) is None


# ── grants ───────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_setting_a_role_twice_updates_rather_than_duplicating(
    client, workspace, principal_maker
):
    member = await principal_maker()
    cid = workspace["channel"]["id"]
    await wsr.set_role(channel_id=cid, principal_id=member["id"], role="contributor")
    await wsr.set_role(channel_id=cid, principal_id=member["id"], role="operator")
    roster = await wsr.list_members(cid)
    mine = [r for r in roster if r["principal_id"] == member["id"]]
    assert len(mine) == 1 and mine[0]["role"] == "operator"


@pytest.mark.asyncio
async def test_an_unknown_role_is_refused(client, workspace, principal_maker):
    member = await principal_maker()
    with pytest.raises(ValueError):
        await wsr.set_role(channel_id=workspace["channel"]["id"],
                           principal_id=member["id"], role="admiral")


@pytest.mark.asyncio
async def test_the_roster_is_ordered_by_rank_not_alphabetically(
    client, workspace, principal_maker
):
    cid = workspace["channel"]["id"]
    low, high = await principal_maker(), await principal_maker()
    await wsr.set_role(channel_id=cid, principal_id=low["id"], role="observer")
    await wsr.set_role(channel_id=cid, principal_id=high["id"], role="lead")
    roster = await wsr.list_members(cid)
    assert roster[0]["role"] == "lead"


# ── templates ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_applying_a_template_grants_its_role_and_its_bundle(
    client, workspace, principal_maker
):
    member = await principal_maker()
    result = await wsr.apply_template(
        channel_id=workspace["channel"]["id"], principal_id=member["id"],
        template_name="engineer", guild_id=workspace["guild_id"],
    )
    assert result["role"] == "operator"
    assert "git_push" in result["allowed_tools"]
    assert result["budget_usdc"] > 0


@pytest.mark.asyncio
async def test_an_unknown_template_is_refused(client, workspace, principal_maker):
    member = await principal_maker()
    with pytest.raises(ValueError):
        await wsr.apply_template(channel_id=workspace["channel"]["id"],
                                 principal_id=member["id"], template_name="wizard")


@pytest.mark.asyncio
async def test_seeding_templates_twice_installs_them_once(client):
    await wsr.seed_builtin_templates()
    names = [t["name"] for t in await wsr.list_templates()]
    assert len(names) == len(set(names))


@pytest.mark.asyncio
async def test_a_guild_template_shadows_the_instance_wide_one(client, workspace):
    async with get_db() as db:
        await db.execute(
            """INSERT INTO role_templates
                 (guild_id, name, description, workspace_role, skills, allowed_tools, budget_usdc)
               VALUES (?,'engineer','Stricter here','contributor','[]','[]',0)""",
            (workspace["guild_id"],),
        )
        await db.commit()
    tpl = await wsr.get_template("engineer", workspace["guild_id"])
    assert tpl["workspace_role"] == "contributor"
    # and the instance-wide one is untouched, so other guilds keep the default
    assert (await wsr.get_template("engineer", None))["workspace_role"] == "operator"


@pytest.mark.asyncio
async def test_every_builtin_template_names_a_real_role(client):
    for tpl in wsr.BUILTIN_TEMPLATES:
        assert tpl["workspace_role"] in wsr.RANK
