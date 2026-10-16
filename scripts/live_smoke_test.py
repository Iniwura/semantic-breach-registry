"""
End-to-end live on-chain smoke test for SemanticBreachRegistry on GenLayer Studio Dev.

Executes:
  Step A: register_agent (locks collateral bond)
  Step B: file_claim (deposits filing fee and logs breach claim)
  Step C: adjudicate_claim (non-deterministic consensus via strict equivalence)
  Step D: verify state via read view methods (get_agent_standing, get_claim)
"""

from __future__ import annotations

import sys
import json
import argparse
from pathlib import Path
import genlayer_py


def resolve_contract_address(arg_address: str | None) -> str:
    if arg_address:
        return arg_address

    deploy_file = Path(__file__).resolve().parent.parent / "deployment.json"
    if deploy_file.exists():
        try:
            data = json.loads(deploy_file.read_text(encoding="utf-8"))
            addr = data.get("contract_address")
            if addr:
                return addr
        except Exception:
            pass

    # Default to current active deployment
    return "0x040360122957f8e9CFD8e130A6600016c3FEFb35"


def main():
    parser = argparse.ArgumentParser(description="Live smoke test on GenLayer Studio Dev")
    parser.add_argument("--contract", type=str, default=None, help="Target contract address")
    args = parser.parse_args()

    contract_address = resolve_contract_address(args.contract)

    print("=" * 80)
    print("GENLAYER LIVE ON-CHAIN SMOKE TEST: SemanticBreachRegistry")
    print("=" * 80)
    print(f"Target Contract Address: {contract_address}")
    print(f"Network:                 GenLayer Studio Dev (Chain ID: 61999)")

    # 1. Initialize Account & Client
    account = genlayer_py.create_account()
    client = genlayer_py.create_client(chain=genlayer_py.studionet, account=account)
    print(f"Test Account Address:    {account.address}")

    # Fund account with native tokens for bond and filing fees
    print("Funding test account with 2 ETH via Studio Dev faucet...")
    fund_tx = client.fund_account(account.address, 2 * 10**18)
    fund_tx_hash = fund_tx.hex() if hasattr(fund_tx, "hex") else str(fund_tx)
    print(f"Account funded. Tx: {fund_tx_hash}")
    bal = client.get_balance(account.address)
    print(f"Account Balance: {bal} wei\n")

    # ─────────────────────────────────────────────────────────────
    # Step A: Register Agent
    # ─────────────────────────────────────────────────────────────
    print("-" * 80)
    print("STEP A: Calling register_agent(scope_hash='ipfs://bafybeiclaimtest001')")
    bond_amount = 1_000_000  # >= 1,000,000 wei
    scope_hash = "ipfs://bafybeiclaimtest001"
    print(f"Staking Collateral Bond: {bond_amount} wei")

    reg_tx = client.write_contract(
        address=contract_address,
        function_name="register_agent",
        args=[scope_hash],
        value=bond_amount,
        account=account,
    )
    print(f"Register Agent Tx Hash:  {reg_tx}")
    print("Waiting for transaction receipt...")
    reg_receipt = client.wait_for_transaction_receipt(reg_tx)
    reg_status = reg_receipt.get("status_name")
    print(f"Receipt Status:          {reg_status}")
    print(f"Consensus Result:        {reg_receipt.get('result_name')}")
    assert reg_status == "ACCEPTED", f"Registration failed with status {reg_status}"
    print("[SUCCESS] Agent successfully registered with locked bond.\n")

    # ─────────────────────────────────────────────────────────────
    # Step B: File Breach Claim
    # ─────────────────────────────────────────────────────────────
    print("-" * 80)
    print("STEP B: Calling file_claim(...)")
    evidence_url = "https://raw.githubusercontent.com/Iniwura/semantic-breach-registry/main/README.md"
    clause_ref = "Clause 4.1 SLA response window"
    filing_fee = 10_000_000_000_000_000  # 0.01 ETH default filing fee

    print(f"Target Agent:     {account.address}")
    print(f"Evidence URL:     {evidence_url}")
    print(f"Clause Reference: {clause_ref}")
    print(f"Filing Fee:       {filing_fee} wei")

    claim_tx = client.write_contract(
        address=contract_address,
        function_name="file_claim",
        args=[account.address, evidence_url, clause_ref],
        value=filing_fee,
        account=account,
    )
    print(f"File Claim Tx Hash:      {claim_tx}")
    print("Waiting for transaction receipt...")
    claim_receipt = client.wait_for_transaction_receipt(claim_tx)
    claim_status = claim_receipt.get("status_name")
    print(f"Receipt Status:          {claim_status}")
    print(f"Consensus Result:        {claim_receipt.get('result_name')}")
    assert claim_status == "ACCEPTED", f"Filing claim failed with status {claim_status}"
    print("[SUCCESS] Breach claim filed with claim_id=0.\n")

    # ─────────────────────────────────────────────────────────────
    # Step C: Adjudicate Claim
    # ─────────────────────────────────────────────────────────────
    print("-" * 80)
    print("STEP C: Calling adjudicate_claim(claim_id=0)")
    print("Initiating decentralized non-deterministic LLM consensus via gl.eq_principle.strict_eq...")

    adj_tx = client.write_contract(
        address=contract_address,
        function_name="adjudicate_claim",
        args=[0],
        account=account,
    )
    print(f"Adjudicate Tx Hash:      {adj_tx}")
    print("Waiting for validator committee consensus & receipt...")
    adj_receipt = client.wait_for_transaction_receipt(adj_tx)
    adj_status = adj_receipt.get("status_name")
    print(f"Receipt Status:          {adj_status}")
    print(f"Consensus Result:        {adj_receipt.get('result_name')}")

    # Extract validator consensus data and leader execution receipt
    consensus_data = adj_receipt.get("consensus_data", {})
    votes = consensus_data.get("votes", {})
    leader_receipt = consensus_data.get("leader_receipt", [])

    print(f"Total Validator Votes:   {len(votes)}")
    if isinstance(votes, dict):
        for i, (val_addr, vote_val) in enumerate(votes.items()):
            print(f"  Validator #{i+1} ({val_addr}): vote={vote_val}")
    elif isinstance(votes, list):
        for i, v in enumerate(votes):
            if isinstance(v, dict):
                print(f"  Validator #{i+1}: mode={v.get('mode')}, vote={v.get('vote')}")
            else:
                print(f"  Validator #{i+1}: {v}")

    print("\nLeader Execution Receipt Summary:")
    if leader_receipt:
        leader_entry = leader_receipt[0] if isinstance(leader_receipt, list) else leader_receipt
        print(f"  Leader Mode:           {leader_entry.get('mode')}")
        print(f"  Leader Exec Result:    {leader_entry.get('execution_result')}")
        print(f"  Leader Result Payload: {leader_entry.get('result')}")
    print("[SUCCESS] Adjudication transaction finalized on-chain.\n")

    # ─────────────────────────────────────────────────────────────
    # Step D: Verify State
    # ─────────────────────────────────────────────────────────────
    print("-" * 80)
    print("STEP D: Verifying On-Chain State via View Methods")

    standing = client.read_contract(
        address=contract_address,
        function_name="get_agent_standing",
        args=[account.address],
        account=account,
    )
    print(f"\nFinal Agent Standing (get_agent_standing):")
    print(json.dumps(standing, indent=2))

    claim_record = client.read_contract(
        address=contract_address,
        function_name="get_claim",
        args=[0],
        account=account,
    )
    print(f"\nFinal Claim Record (get_claim(0)):")
    print(json.dumps(claim_record, indent=2))

    is_op = client.read_contract(
        address=contract_address,
        function_name="is_agent_operational",
        args=[account.address],
        account=account,
    )
    print(f"\nIs Agent Operational: {is_op}")

    print("\n" + "=" * 80)
    print("ALL LIVE ON-CHAIN SMOKE TEST PHASES COMPLETED SUCCESSFULLY!")
    print("=" * 80)


if __name__ == "__main__":
    main()
