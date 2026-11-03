"""Research playbooks: they parse, voice words find them, and the product context reads only what it should."""
from app import research
from tests.conftest import tmp_root


def test_builtin_playbooks_parse():
    pbs = research.playbooks()
    assert {"competition", "market", "features", "users"} <= set(pbs)
    for pb in pbs.values():
        assert pb["template"].startswith("## Findings") and "## Sources" in pb["template"]
        assert "{product}" in pb["instructions"]
        assert 0 < pb["budget_usd"] <= 5


def test_voice_words_pick_the_playbook():
    assert research.kind_for("competitors") == "competition"
    assert research.kind_for("reddit") == "users"
    assert research.kind_for("end user") == "users"
    assert research.kind_for("market sizing") == "market"
    assert research.kind_for("spec") == "features"
    assert research.kind_for("astrology") is None


def test_user_playbook_overrides_and_adds(monkeypatch):
    d = tmp_root() / "pb"
    d.mkdir()
    monkeypatch.setattr(research, "USER_DIR", d)
    (d / "pricing.md").write_text("---\nkind: pricing\nlabel: Pricing research\nvoice: pricing\n---\n"
                                  "Research pricing for {product}.\n\n## Report\n\n## Findings\n\n## Sources\n")
    pbs = research.playbooks()
    assert pbs["pricing"]["custom"] and research.kind_for("pricing") == "pricing"


def test_product_context_reads_only_safe_files():
    proj = tmp_root() / "acme"
    proj.mkdir()
    (proj / "README.md").write_text("# Acme\nInvoices for freelancers.")
    (proj / "package.json").write_text('{"name": "acme", "description": "Invoicing for freelancers"}')
    (proj / ".env").write_text("STRIPE_SECRET=sk_live_should_never_appear")
    (proj / "server.js").write_text("const secret = 'in source code';")
    ctx = research.product_context({"name": "acme", "cwd": str(proj), "info": {"title": "Recurring invoices"}},
                                   ["Add recurring invoices", "<command-name>/clear</command-name>"])
    assert "Invoices for freelancers" in ctx and "Invoicing for freelancers" in ctx and "Recurring invoices" in ctx
    assert "sk_live" not in ctx and "in source code" not in ctx
    assert len(ctx) <= research.CONTEXT_LIMIT


def test_render_fills_placeholders():
    pb = research.playbooks()["users"]
    brief, template = research.render(pb, "acme", "Product / project name: acme", "freelancers")
    assert "{product}" not in brief + template and "{focus}" not in brief
    assert "acme" in template and "Focus especially on: freelancers" in brief
