// SPDX-License-Identifier: MIT
pragma solidity ^0.8.19;

contract GasPaymaster {
    address public immutable owner;
    uint256 public grossProfit;
    uint256 public gasDebt;
    uint256 public loanFees;
    uint256 public netAccumulated;

    event ProfitRecorded(uint256 gross, uint256 gasCost, uint256 loanFee, uint256 net);
    event PayoutExecuted(address indexed recipient, uint256 amount);

    constructor() {
        owner = msg.sender;
    }

    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    function recordTrade(
        uint256 _grossProfit,
        uint256 _gasCost,
        uint256 _loanFee
    ) external onlyOwner {
        grossProfit += _grossProfit;
        gasDebt += _gasCost;
        loanFees += _loanFee;
        uint256 net = _grossProfit - _gasCost - _loanFee;
        netAccumulated += net;
        emit ProfitRecorded(_grossProfit, _gasCost, _loanFee, net);
    }

    function payout(address recipient) external onlyOwner {
        uint256 amount = netAccumulated;
        require(amount > 0, "No profit to pay");
        netAccumulated = 0;
        grossProfit = 0;
        gasDebt = 0;
        loanFees = 0;
        payable(recipient).transfer(amount);
        emit PayoutExecuted(recipient, amount);
    }

    function getLedger() external view returns (uint256, uint256, uint256, uint256) {
        return (grossProfit, gasDebt, loanFees, netAccumulated);
    }

    receive() external payable {}
}
