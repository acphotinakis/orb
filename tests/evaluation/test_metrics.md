# Test Metrics Documentation

## Overview

This documentation provides detailed information about the unit tests for the performance metrics calculation functions in `src.evaluation.metrics`. The tests cover the core functionality for computing trade metrics, portfolio metrics, and complete performance reports.

## Test Structure

The tests are organized into three main sections:
1. **Trade Metrics Tests** - Testing calculations related to individual trades
2. **Portfolio Metrics Tests** - Testing time-series performance and risk metrics
3. **Performance Report Tests** - Testing the complete report generation workflow

## Trade Metrics Tests

### `test_calculate_trade_metrics_empty_dataframe`
**Purpose**: Tests the behavior of `calculate_trade_metrics` when passed an empty DataFrame.

**Details**:
- Tests edge case handling for empty data
- Verifies that all metrics return appropriate default values (zeros)
- Ensures that division-by-zero scenarios are handled gracefully

**Expected Results**:
- All numeric metrics return 0.0
- All count metrics return 0
- Exit reasons dictionary shows 0 counts for all reasons
- No exceptions are raised

### `test_calculate_trade_metrics_all_wins`
**Purpose**: Tests trade metrics calculation with all winning trades.

**Details**:
- Creates a DataFrame with 3 winning trades (LONG and SHORT directions)
- Tests positive R-multiples and positive PnL values
- Verifies calculation of performance metrics like win rate, profit factor, and expectancy

**Expected Results**:
- Win count = 3, Loss count = 0, Scratch count = 0
- Win rate = 1.0 (100%)
- Profit factor = infinity (since gross losses = 0)
- All positive metrics are calculated correctly
- Best and worst trade values reflect winning trades

### `test_calculate_trade_metrics_all_losses`
**Purpose**: Tests trade metrics calculation with all losing trades.

**Details**:
- Creates a DataFrame with 3 losing trades
- Tests negative R-multiples and negative PnL values
- Verifies calculation of loss-related metrics and ratios

**Expected Results**:
- Win count = 0, Loss count = 3, Scratch count = 0
- Win rate = 0.0 (0%)
- Profit factor = 0.0 (since gross profits = 0)
- All negative metrics are calculated correctly
- Best and worst trade values reflect losing trades

### `test_calculate_trade_metrics_mixed`
**Purpose**: Tests trade metrics calculation with a mix of winning and losing trades.

**Details**:
- Creates a DataFrame with 4 trades: 2 wins, 2 losses
- Tests a balanced portfolio with proper metric calculations
- Verifies complex calculations like expectancy and payoff ratio

**Expected Results**:
- Win count = 2, Loss count = 2, Scratch count = 0
- Win rate = 0.5 (50%)
- Profit factor = 2.0 (gross profits / gross losses = 300 / 150)
- Expectancy calculations are accurate (0.5 * 3.0 - 0.5 * 1.5 = 0.75)
- Payoff ratio = 2.0 (avg win / avg loss = 150 / 75)

### `test_calculate_trade_metrics_scratch_trades`
**Purpose**: Tests trade metrics calculation with scratch trades (zero PnL trades).

**Details**:
- Creates a DataFrame with 4 trades including one scratch trade (PnL = 0)
- Tests the proper handling of scratch trades in calculations
- Verifies that scratch trades are counted separately from wins/losses

**Expected Results**:
- Win count = 2, Loss count = 1, Scratch count = 1
- Win rate = 0.5 (50%)
- Profit factor = 3.0 (gross profits / gross losses = 300 / 100)
- Proper handling of zero PnL trades in calculations

## Portfolio Metrics Tests

### `test_calculate_portfolio_metrics_empty_dataframe`
**Purpose**: Tests portfolio metrics calculation with empty DataFrame.

**Details**:
- Tests edge case handling for empty equity data
- Verifies default return values for all portfolio metrics
- Ensures graceful handling of missing data

**Expected Results**:
- All metrics return default values (0.0 or 0)
- Initial capital and ending equity are set to default (100000.0)
- No exceptions are raised

### `test_calculate_portfolio_metrics_single_equity`
**Purpose**: Tests portfolio metrics calculation with single equity value.

**Details**:
- Tests minimal equity data scenario
- Verifies that a single equity value produces reasonable metrics
- Validates constant returns (0% in this case)

**Expected Results**:
- Initial and ending equity are the same
- Total return percentage = 0.0
- All ratios return 0.0 due to zero variance

### `test_calculate_portfolio_metrics_with_drawdown`
**Purpose**: Tests portfolio metrics calculation with equity data that includes drawdown.

**Details**:
- Creates equity data with a peak-to-trough decline (10% drawdown)
- Tests max drawdown percentage and dollar calculations
- Validates drawdown duration calculations

**Expected Results**:
- Max drawdown percentage = 10.0
- Max drawdown dollars = 10000.0
- Max drawdown duration = 2 bars

### `test_calculate_portfolio_metrics_with_cagr`
**Purpose**: Tests portfolio metrics calculation with compound annual growth rate (CAGR) data.

**Details**:
- Creates equity data with 10% annual growth over 4 years
- Tests CAGR calculations and validates results
- Verifies that CAGR approaches the expected growth rate

**Expected Results**:
- Initial and ending equity reflect the growth
- Total return percentage = 46.41%
- CAGR calculation produces approximately 10%

## Performance Report Tests

### `test_generate_performance_report`
**Purpose**: Tests the complete performance report generation workflow.

**Details**:
- Tests integration of trade and portfolio metrics
- Validates that all data flows through the complete report generation
- Ensures correct structure and data mapping

**Expected Results**:
- Report contains all expected sections (strategy, trade_metrics, portfolio_metrics)
- Strategy information is correctly mapped from config
- Trade metrics reflect the mixed win/loss data
- Portfolio metrics are calculated correctly

## Test Best Practices and Considerations

### Data Validation
All tests ensure that:
- Edge cases are properly handled (empty DataFrames, all wins, all losses, etc.)
- Mathematical formulas are applied correctly
- Floating-point comparisons use appropriate rounding for accuracy
- Invalid calculations result in sensible default values

### Test Coverage
The test suite provides comprehensive coverage of:
- Normal scenarios
- Edge cases (empty data, boundary conditions)
- Special cases (all wins, all losses, scratch trades)
- Error handling scenarios

### Metric Calculations
Key metrics tested include:
- **Win Rate**: Percentage of winning trades
- **Profit Factor**: Gross profits / Gross losses
- **Expectancy**: Mathematical expectation of trades (E_R = W * avg_win_R - (1 - W) * avg_loss_R)
- **Payoff Ratio**: Average win / Average loss
- **Sharpe Ratio**: Risk-adjusted return measure
- **Sortino Ratio**: Downside risk-adjusted return measure
- **Calmar Ratio**: CAGR / Max Drawdown
- **Max Drawdown**: Maximum peak-to-trough decline

Each metric calculation is verified through comprehensive test cases to ensure correctness and handle various edge cases.
