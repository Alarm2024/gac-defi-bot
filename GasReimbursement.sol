// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

/**
 * 🪬🧿✝️  GasReimbursement
 *         Deferred Gas Settlement for Garden Angel
 *         Owner (bot) records profits & gas debts; payout releases net.
 *         CEI pattern enforced | address(this).balance assertions
 *         Contract net = totalProfit - totalGasDebt - adminFee
 *         (Loan fees are off-chain and not tracked on-chain)
 */
contract GasReimbursement {
    address public owner;
    uint256 public totalProfit;
    uint256 public totalGasDebt;
    uint256 public constant ADMIN_FEE_PCT = 0; // can be made settable

    event ProfitRecorded(uint256 amount);
    event GasDebtRecorded(uint256 amount);
    event Payout(address indexed recipient, uint256 netAmount, uint256 balanceAfter);

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    constructor() {
        owner = msg.sender;
    }

    function recordProfit(uint256 amount) external onlyOwner {
        totalProfit += amount;
        emit ProfitRecorded(amount);
    }

    function recordGasDebt(uint256 amount) external onlyOwner {
        totalGasDebt += amount;
        emit GasDebtRecorded(amount);
    }

    function recordProfitAndGas(uint256 profit, uint256 gasDebt) external onlyOwner {
        totalProfit += profit;
        totalGasDebt += gasDebt;
        emit ProfitRecorded(profit);
        emit GasDebtRecorded(gasDebt);
    }

    function payout(address recipient, uint256 maxNet) external onlyOwner {
        uint256 adminFee = (totalProfit * ADMIN_FEE_PCT) / 100;
        uint256 net = totalProfit - totalGasDebt - adminFee;
        require(net > 0, "No net profit");
        require(net <= maxNet, "Net exceeds max (safety)");

        uint256 contractBalance = address(this).balance;
        require(contractBalance >= net, "Insufficient contract balance");

        totalProfit = 0;
        totalGasDebt = 0;

        (bool success, ) = recipient.call{value: net}("");
        require(success, "Transfer failed");

        emit Payout(recipient, net, address(this).balance);
    }

    function withdrawExcess(address to) external onlyOwner {
        uint256 balance = address(this).balance;
        uint256 expected = totalProfit - totalGasDebt - (totalProfit * ADMIN_FEE_PCT / 100);
        require(balance > expected, "No excess");
        uint256 excess = balance - expected;
        (bool success, ) = to.call{value: excess}("");
        require(success, "Excess withdrawal failed");
    }

    receive() external payable {}
}
