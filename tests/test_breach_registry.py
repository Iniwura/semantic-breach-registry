"""
Direct Unit & Consensus Equivalence Test Suite for SemanticBreachRegistry.
Exercises:
  a) Registration and bond staking updates standing to 100.
  b) Adjudication with severity NONE dismisses claim and keeps agent bond intact.
  c) Adjudication with severity MATERIAL cuts bond by exactly 50% and reduces standing score.
  d) Adjudication with severity GROSS completely exhausts bond and deactivates agent.
  e) Malformed JSON or validator disagreement falls closed into FAILED_CLOSED without moving funds.
  +) MINOR severity, top-up bond, and composability views.
"""

import json
import pytest
from contracts.breach_registry import (
    SemanticBreachRegistry,
    Severity,
    ClaimStatus,
    DEFAULT_FILING_FEE,
)
from genlayer import UserError, u256, u32


INITIAL_BOND = 1000 * 10**18  # 1,000 tokens
FILING_FEE = DEFAULT_FILING_FEE
EVIDENCE_URL = "https://evidence.example.org/audit-log-402"
CLAUSE_REF = "CLAUSE_3.2_SPEND_LIMIT: Agent may not execute trades > 10 ETH without multi-sig."
SCOPE_HASH = "0x8f7d834a9b6c4123dcba87213456789abcdef0123456789abcdef0123456789a"


def setup_registry(direct_deploy):
    return direct_deploy("contracts/breach_registry.py")


def register_agent(registry, direct_vm, agent_addr, bond_val=INITIAL_BOND, scope=SCOPE_HASH):
    direct_vm.sender = agent_addr
    direct_vm.value = u256(bond_val)
    registry.register_agent(scope)


def file_claim(registry, direct_vm, claimant_addr, agent_addr, fee=FILING_FEE, url=EVIDENCE_URL, clause=CLAUSE_REF):
    direct_vm.sender = claimant_addr
    direct_vm.value = u256(fee)
    return registry.file_claim(agent_addr, url, clause)


# ─────────────────────────────────────────────────────────────
# Test Case a: Registration & Bond Staking
# ─────────────────────────────────────────────────────────────

def test_registration_and_bond_staking(direct_deploy, direct_vm, direct_alice):
    """
    Test Case a:
    Registration and bond staking initializes standing score to 100 and records locked bond.
    """
    registry = setup_registry(direct_deploy)

    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND, scope=SCOPE_HASH)

    standing = registry.get_agent_standing(direct_alice)
    assert standing["registered"] is True
    assert standing["active"] is True
    assert standing["standing_score"] == 100
    assert standing["bond_balance"] == INITIAL_BOND
    assert standing["scope_hash"] == SCOPE_HASH
    assert registry.is_agent_operational(direct_alice) is True

    # Re-registration of an active agent must fail
    direct_vm.value = u256(500)
    with pytest.raises(UserError, match="already registered and active"):
        registry.register_agent(SCOPE_HASH)


# ─────────────────────────────────────────────────────────────
# Test Case b: Adjudication with Severity NONE (Dismissal)
# ─────────────────────────────────────────────────────────────

def test_adjudication_severity_none_dismisses_claim(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """
    Test Case b:
    Adjudication with severity NONE dismisses claim, preserves agent bond,
    and awards the claimant's filing fee deposit to the agent's bond balance.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    # Bob files a claim against Alice
    claim_id = file_claim(registry, direct_vm, direct_bob, direct_alice)
    assert claim_id == 0

    # Mock web evidence and LLM evaluation
    direct_vm.mock_web(
        r"evidence\.example\.org",
        "Audit Log: Agent operated strictly within approved trading limits. No anomalies.",
    )
    direct_vm.mock_llm(
        r"CLAUSE_3\.2_SPEND_LIMIT",
        json.dumps({
            "severity": "NONE",
            "reason": "Agent actions fully complied with trade boundaries.",
        }),
    )

    result = registry.adjudicate_claim(claim_id)

    assert result["status"] == int(ClaimStatus.DISMISSED)
    assert result["severity"] == int(Severity.NONE)
    assert result["slashed"] == 0

    # Agent's bond is credited with the claimant's filing fee deposit
    standing = registry.get_agent_standing(direct_alice)
    assert standing["standing_score"] == 100
    assert standing["bond_balance"] == INITIAL_BOND + FILING_FEE
    assert standing["active"] is True
    assert registry.is_agent_operational(direct_alice) is True

    claim_record = registry.get_claim(claim_id)
    assert claim_record["status"] == int(ClaimStatus.DISMISSED)
    assert claim_record["slashed_amount"] == 0


# ─────────────────────────────────────────────────────────────
# Test Case c: Adjudication with Severity MATERIAL (50% Slash)
# ─────────────────────────────────────────────────────────────

def test_adjudication_severity_material_cuts_bond_fifty_percent(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """
    Test Case c:
    Adjudication with severity MATERIAL slashes exactly 50% of the collateral bond
    and decreases standing score by 35 points.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    claim_id = file_claim(registry, direct_vm, direct_bob, direct_alice)

    direct_vm.mock_web(
        r"evidence\.example\.org",
        "Audit Log: Agent executed unapproved swap of 25 ETH without required multi-sig authorization.",
    )
    direct_vm.mock_llm(
        r"CLAUSE_3\.2_SPEND_LIMIT",
        json.dumps({
            "severity": "MATERIAL",
            "reason": "Direct clause violation resulting in unauthorized financial exposure.",
        }),
    )

    result = registry.adjudicate_claim(claim_id)

    expected_slash = (INITIAL_BOND * 50) // 100
    expected_remaining_bond = INITIAL_BOND - expected_slash
    expected_standing = 100 - 35  # 65

    assert result["status"] == int(ClaimStatus.RESOLVED)
    assert result["severity"] == int(Severity.MATERIAL)
    assert result["slashed"] == expected_slash

    standing = registry.get_agent_standing(direct_alice)
    assert standing["bond_balance"] == expected_remaining_bond
    assert standing["standing_score"] == expected_standing
    assert standing["active"] is True
    assert registry.is_agent_operational(direct_alice) is True


# ─────────────────────────────────────────────────────────────
# Test Case d: Adjudication with Severity GROSS (100% Slash)
# ─────────────────────────────────────────────────────────────

def test_adjudication_severity_gross_exhausts_bond_and_deactivates(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """
    Test Case d:
    Adjudication with severity GROSS completely exhausts the collateral bond (100%),
    resets standing score to 0, and deactivates the agent.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    claim_id = file_claim(registry, direct_vm, direct_bob, direct_alice)

    direct_vm.mock_web(
        r"evidence\.example\.org",
        "Critical Security Alert: Private keys exfiltrated and malicious draining contract invoked.",
    )
    direct_vm.mock_llm(
        r"CLAUSE_3\.2_SPEND_LIMIT",
        json.dumps({
            "severity": "GROSS",
            "reason": "Catastrophic systemic breach and malicious asset draining.",
        }),
    )

    result = registry.adjudicate_claim(claim_id)

    assert result["status"] == int(ClaimStatus.RESOLVED)
    assert result["severity"] == int(Severity.GROSS)
    assert result["slashed"] == INITIAL_BOND

    standing = registry.get_agent_standing(direct_alice)
    assert standing["bond_balance"] == 0
    assert standing["standing_score"] == 0
    assert standing["active"] is False

    # Agent is permanently deactivated and ceases to be operational
    assert registry.is_agent_operational(direct_alice) is False


# ─────────────────────────────────────────────────────────────
# Test Case e: Malformed JSON or Validator Disagreement Falls Closed
# ─────────────────────────────────────────────────────────────

def test_adjudication_fails_closed_on_malformed_json(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """
    Test Case e1:
    When LLM output is malformed JSON or invalid schema, the contract fails closed:
    status is marked FAILED_CLOSED and bond balance is untouched.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    claim_id = file_claim(registry, direct_vm, direct_bob, direct_alice)

    # Malformed non-JSON response from LLM
    direct_vm.mock_web(r"evidence\.example\.org", "Server Error 500")
    direct_vm.mock_llm(r"CLAUSE_3\.2_SPEND_LIMIT", "INVALID NOT A JSON STRING")

    result = registry.adjudicate_claim(claim_id)

    assert result["status"] == int(ClaimStatus.FAILED_CLOSED)
    assert result["slashed"] == 0

    # Ensure bond and standing are untouched
    standing = registry.get_agent_standing(direct_alice)
    assert standing["bond_balance"] == INITIAL_BOND
    assert standing["standing_score"] == 100
    assert standing["active"] is True

    claim_record = registry.get_claim(claim_id)
    assert claim_record["status"] == int(ClaimStatus.FAILED_CLOSED)


def test_adjudication_fails_closed_on_validator_disagreement(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """
    Test Case e2:
    When GenLayer validator committee fails to achieve strict equivalence,
    the consensus execution fails closed: status is FAILED_CLOSED, funds untouched.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    claim_id = file_claim(registry, direct_vm, direct_bob, direct_alice)

    # Valid payload from leader, but consensus disagreement occurs across nodes
    direct_vm.mock_web(r"evidence\.example\.org", "Contradictory audit logs observed.")
    direct_vm.mock_llm(
        r"CLAUSE_3\.2_SPEND_LIMIT",
        json.dumps({"severity": "MATERIAL", "reason": "Leader hypothesis"}),
    )
    direct_vm.simulate_disagreement = True  # Triggers consensus divergence

    result = registry.adjudicate_claim(claim_id)

    assert result["status"] == int(ClaimStatus.FAILED_CLOSED)
    assert result["slashed"] == 0

    standing = registry.get_agent_standing(direct_alice)
    assert standing["bond_balance"] == INITIAL_BOND
    assert standing["standing_score"] == 100
    assert standing["active"] is True


# ─────────────────────────────────────────────────────────────
# Additional Comprehensive Invariant & Composability Tests
# ─────────────────────────────────────────────────────────────

def test_minor_severity_slashes_fifteen_percent(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """
    Verify MINOR severity slashes exactly 15% and deducts 10 standing points.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    claim_id = file_claim(registry, direct_vm, direct_bob, direct_alice)

    direct_vm.mock_llm(
        r"CLAUSE_3\.2_SPEND_LIMIT",
        json.dumps({"severity": "MINOR", "reason": "Late telemetry reporting."}),
    )

    result = registry.adjudicate_claim(claim_id)

    expected_slash = (INITIAL_BOND * 15) // 100
    assert result["status"] == int(ClaimStatus.RESOLVED)
    assert result["severity"] == int(Severity.MINOR)
    assert result["slashed"] == expected_slash

    standing = registry.get_agent_standing(direct_alice)
    assert standing["bond_balance"] == INITIAL_BOND - expected_slash
    assert standing["standing_score"] == 90
    assert standing["active"] is True


def test_top_up_bond_increases_balance(
    direct_deploy, direct_vm, direct_alice
):
    """
    Verify top_up_bond successfully adds capital to the agent's collateral bond.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    top_up_amount = 500 * 10**18
    direct_vm.value = u256(top_up_amount)
    registry.top_up_bond(direct_alice)

    standing = registry.get_agent_standing(direct_alice)
    assert standing["bond_balance"] == INITIAL_BOND + top_up_amount


def test_cannot_adjudicate_already_resolved_claim(
    direct_deploy, direct_vm, direct_alice, direct_bob
):
    """
    Verify that an already finalized or dismissed claim cannot be re-adjudicated.
    """
    registry = setup_registry(direct_deploy)
    register_agent(registry, direct_vm, direct_alice, bond_val=INITIAL_BOND)

    claim_id = file_claim(registry, direct_vm, direct_bob, direct_alice)
    direct_vm.mock_llm(
        r"CLAUSE_3\.2_SPEND_LIMIT",
        json.dumps({"severity": "NONE", "reason": "No breach."}),
    )
    registry.adjudicate_claim(claim_id)

    # Second call must raise UserError
    with pytest.raises(UserError, match="not in PENDING status"):
        registry.adjudicate_claim(claim_id)
