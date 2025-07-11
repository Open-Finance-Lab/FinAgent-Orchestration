"""
Entry point for starting the agent, supports direct command-line execution.
All comments in this file are in English for clarity and maintainability.
"""

# agent_pools/alpha_agent_pool/agents/theory_driven/momentum_agent.py

from mcp.server.fastmcp import FastMCP, Context as MCPContext
import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../')))
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../')))
from schema.theory_driven_schema import (
    MomentumAgentConfig, MomentumSignalRequest, AlphaStrategyFlow, MarketContext, Decision, Action, PerformanceFeedback, Metadata
)
from typing import List, Dict, Any, Optional
import asyncio
import sys
import argparse
import json
import os
from datetime import datetime
import hashlib
import logging

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    # Load .env from project root
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '..'))
    env_path = os.path.join(project_root, '.env')
    load_dotenv(env_path)
    print(f"✅ Loaded .env from: {env_path}")
except ImportError:
    print("⚠️ python-dotenv not installed. Please install with: pip install python-dotenv")
except Exception as e:
    print(f"⚠️ Failed to load .env file: {e}")

# LLM Integration
try:
    import openai
    from openai import AsyncOpenAI
    LLM_AVAILABLE = True
except ImportError:
    LLM_AVAILABLE = False
    AsyncOpenAI = None


# Configure logging: always overwrite log file on agent start
log_path = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../momentum_agent.log'))
if os.path.exists(log_path):
    try:
        os.remove(log_path)
    except Exception:
        pass
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(log_path, mode='w', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class MomentumAgent:
    def __init__(self, config: MomentumAgentConfig):
        """
        Initialize the MomentumAgent with LLM integration for intelligent signal generation.
        Args:
            config (MomentumAgentConfig): Configuration object for the agent.
        """
        self.config = config
        self.agent = FastMCP("MomentumAlphaAgent")
        
        # Initialize LLM client
        self.llm_client = None
        self._initialize_llm()
        
        # Path to the shared memory JSON file (used by AlphaAgentPool)
        self.memory_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../memory_unit.json"))
        # Path to store strategy signal flow
        self.signal_flow_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../momentum_signal_flow.json"))
        
        # Clear memory and signal flow files on each agent restart
        for path in [self.signal_flow_path]:
            if os.path.exists(path):
                try:
                    os.remove(path)
                except Exception:
                    pass
                    
        self._register_tools()

    def _initialize_llm(self):
        """Initialize the LLM client for intelligent analysis."""
        logger.info("[DEBUG] Entering _initialize_llm()")
        if not LLM_AVAILABLE:
            logger.warning("LLM dependencies not available")
            return
        try:
            api_key = os.getenv('OPENAI_API_KEY')
            logger.info(f"[DEBUG] OPENAI_API_KEY loaded: {'YES' if api_key else 'NO'}")
            if not api_key:
                logger.warning("OPENAI_API_KEY not found in environment")
                return
            self.llm_client = AsyncOpenAI(api_key=api_key)
            logger.info("✅ LLM client initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {e}", exc_info=True)

    def _analyze_multiple_timeframes(self, prices: List[float]) -> Dict[str, Any]:
        """Analyze momentum across multiple timeframes to make intelligent decisions."""
        if len(prices) < 5:
            # For insufficient data, provide a basic fallback structure
            basic_momentum = self._calculate_momentum(prices, min(len(prices), 2)) if len(prices) >= 2 else 0.0
            basic_volatility = self._calculate_volatility(prices) if len(prices) >= 2 else 0.1
            return {
                "analysis": {"insufficient_data": True},
                "windows": [],
                "best_window": min(5, len(prices)),
                "selected_momentum": basic_momentum,
                "selected_volatility": basic_volatility,
                "windows_tested": [min(5, len(prices))],
                "adaptive_selection": False
            }
        
        # Test multiple lookback windows adaptively based on data length
        max_data = len(prices)
        windows = [w for w in [5, 10, 15, 20, 30, 50] if w <= max_data]
        if not windows:
            windows = [min(5, max_data)]
        
        window_analysis = {}
        
        for window in windows:
            if len(prices) >= window:
                momentum = self._calculate_momentum(prices, window)
                volatility = self._calculate_volatility(prices[-window:])
                
                # Calculate trend strength and consistency
                trend_strength = abs(momentum) / (volatility + 1e-8)
                
                # Calculate trend consistency across the window
                window_prices = prices[-window:]
                short_term_momentum = self._calculate_momentum(window_prices, min(5, len(window_prices)))
                consistency = 1.0 - abs(momentum - short_term_momentum) / (abs(momentum) + 1e-8)
                
                # Signal quality combines strength, low volatility, and consistency
                signal_quality = trend_strength * (1 - min(volatility, 0.5)) * consistency
                
                window_analysis[window] = {
                    "momentum": momentum,
                    "volatility": volatility,
                    "trend_strength": trend_strength,
                    "consistency": consistency,
                    "signal_quality": signal_quality
                }
        
        # Select best window based on signal quality
        if window_analysis:
            best_window = max(window_analysis.keys(), key=lambda w: window_analysis[w]["signal_quality"])
        else:
            best_window = 5
        
        return {
            "analysis": window_analysis,
            "best_window": best_window,
            "selected_momentum": window_analysis.get(best_window, {}).get("momentum", 0.0),
            "selected_volatility": window_analysis.get(best_window, {}).get("volatility", 0.1),
            "windows_tested": list(windows),
            "adaptive_selection": True
        }

    async def _analyze_market_with_llm(self, symbol: str, prices: List[float], context: Dict[str, Any]) -> Dict[str, Any]:
        """Use LLM to analyze market conditions with intelligent multi-timeframe analysis."""
        logger.info(f"[DEBUG] Entering _analyze_market_with_llm for symbol={symbol}, num_prices={len(prices)}")
        if not self.llm_client:
            logger.warning("LLM client not initialized, using fallback analysis.")
            return self._fallback_analysis(symbol, prices, context)

        # Perform multi-timeframe analysis
        timeframe_analysis = self._analyze_multiple_timeframes(prices)
        best_window = timeframe_analysis["best_window"]
        
        # Prepare comprehensive market analysis
        if len(prices) >= 2:
            current_price = prices[-1]
            price_change_5d = (prices[-1] - prices[-min(5, len(prices))]) / prices[-min(5, len(prices))] * 100
            price_change_total = (prices[-1] - prices[0]) / prices[0] * 100
            volatility = timeframe_analysis["selected_volatility"]
            momentum = timeframe_analysis["selected_momentum"]
        else:
            current_price = prices[0] if prices else 0
            price_change_5d = 0
            price_change_total = 0
            volatility = 0
            momentum = 0

        # Create intelligent prompt with multi-timeframe context
        prompt = f"""
Analyze market data for {symbol} using intelligent multi-timeframe momentum analysis:

CURRENT MARKET STATE:
- Current Price: ${current_price:.2f}
- Recent Price Data: {prices[-10:] if len(prices) > 10 else prices}

MULTI-TIMEFRAME ANALYSIS RESULTS:
- Optimal Window Selected: {best_window} periods (from candidates: {timeframe_analysis['windows_tested']})
- Selected Momentum: {momentum:.4f} ({momentum*100:.2f}%)
- Price Change (5d): {price_change_5d:.2f}%
- Total Price Change: {price_change_total:.2f}%
- Volatility: {volatility:.4f}
- Signal Quality Score: {timeframe_analysis['analysis'].get(best_window, {}).get('signal_quality', 0):.4f}

DETAILED WINDOW COMPARISON:
{json.dumps(timeframe_analysis['analysis'], indent=2)}

MARKET CONTEXT: {context}

INTELLIGENT TRADING DECISION REQUIRED:
As an expert quantitative analyst, provide an adaptive trading signal considering:

1. MULTI-TIMEFRAME CONVERGENCE: How do different timeframes align?
2. SIGNAL QUALITY: Is the trend strong and consistent across the optimal window?
3. RISK-ADJUSTED RETURNS: What's the expected return vs. risk?
4. MARKET REGIME ADAPTATION: What type of market environment are we in?
5. EXECUTION SIZING: What position size is appropriate given confidence?

Return ONLY valid JSON with these fields:
{{
  "signal": "BUY|SELL|HOLD",
  "confidence": 0.0-1.0,
  "reasoning": "detailed explanation of multi-timeframe analysis and decision logic",
  "market_regime": "bullish_trend|bearish_trend|neutral|volatile|trending",
  "predicted_return": expected_return_estimate,
  "risk_estimate": 0.0-1.0,
  "key_factors": ["list", "of", "key", "decision", "factors"],
  "selected_timeframe": {best_window},
  "execution_weight": 0.0-1.0
}}

Focus on intelligent adaptation rather than conservative defaults. Use the multi-timeframe analysis to make nuanced decisions.
"""

        logger.info(f"[LLM DEBUG] Selected timeframe: {best_window}, momentum: {momentum:.4f}")
        
        try:
            response = await self.llm_client.chat.completions.create(
                model="o4-mini",
                messages=[
                    {"role": "system", "content": "You are an expert quantitative analyst specializing in multi-timeframe momentum analysis. Make intelligent, adaptive trading decisions."},
                    {"role": "user", "content": prompt}
                ],
            )

            result_text = response.choices[0].message.content
            logger.info(f"[LLM DEBUG] Raw model output: {result_text}")

            # Parse JSON response
            try:
                llm_analysis = json.loads(result_text)
                logger.info(f"[LLM DEBUG] Parsed LLM JSON: {llm_analysis}")
                
                # Add timeframe analysis metadata
                llm_analysis.update({
                    "selected_timeframe": best_window,
                    "timeframe_analysis": timeframe_analysis,
                    "execution_weight": abs(float(llm_analysis.get("confidence", 0.5))),
                    "analysis_source": "llm_multiframe"
                })
                
            except json.JSONDecodeError:
                logger.error(f"LLM raw output cannot be parsed: {result_text}")
                return self._fallback_analysis(symbol, prices, context)

            logger.info(f"[LLM DEBUG] Final intelligent analysis result: {llm_analysis}")
            return llm_analysis

        except Exception as e:
            logger.error(f"LLM analysis failed: {e}", exc_info=True)
            return self._fallback_analysis(symbol, prices, context)

    def _fallback_analysis(self, symbol: str, prices: List[float], context: Dict[str, Any]) -> Dict[str, Any]:
        """Direct momentum analysis without conservative thresholds - let the signal speak for itself."""
        if len(prices) < 2:
            return {
                "signal": "HOLD",
                "confidence": 0.0,
                "reasoning": "Insufficient price data for analysis",
                "market_regime": "neutral",
                "predicted_return": 0.0,
                "risk_estimate": 0.1,
                "key_factors": ["insufficient_data"],
                "execution_weight": 0.0,
                "analysis_source": "fallback",
                "selected_timeframe": 5
            }
        
        # Use intelligent timeframe analysis
        timeframe_analysis = self._analyze_multiple_timeframes(prices)
        best_window = timeframe_analysis["best_window"]
        momentum = timeframe_analysis["selected_momentum"]
        volatility = timeframe_analysis["selected_volatility"]
        
        # DIRECT SIGNAL GENERATION - NO CONSERVATIVE THRESHOLDS
        signal = "HOLD"
        confidence = 0.0
        
        # Get trend quality safely
        if isinstance(timeframe_analysis["analysis"], dict) and best_window in timeframe_analysis["analysis"]:
            trend_quality = timeframe_analysis["analysis"][best_window]["signal_quality"]
        else:
            trend_quality = abs(momentum) / (volatility + 1e-8) * 0.5
        
        # DIRECT MOMENTUM-BASED SIGNALS (no arbitrary thresholds)
        if momentum > 0.005:  # Minimal threshold - 0.5% to filter noise only
            signal = "BUY"
            confidence = min(0.95, max(0.3, trend_quality * 3))  # Scale up confidence
        elif momentum < -0.005:  # Minimal threshold - 0.5% to filter noise only  
            signal = "SELL"
            confidence = min(0.95, max(0.3, trend_quality * 3))  # Scale up confidence
        else:
            signal = "HOLD"
            confidence = max(0.2, min(0.6, trend_quality * 2))  # Base confidence for HOLD
        
        # Market regime based on momentum direction
        if momentum > 0.002:
            regime = "bullish_trend"
        elif momentum < -0.002:
            regime = "bearish_trend" 
        elif volatility > 0.04:
            regime = "volatile"
        else:
            regime = "neutral"
        
        # Build key factors
        key_factors = [
            f"timeframe_{best_window}",
            f"momentum_{momentum:.4f}",
            f"volatility_{volatility:.4f}",
            f"quality_{trend_quality:.4f}",
            "direct_signal_no_smoothing"
        ]
        
        return {
            "signal": signal,
            "confidence": confidence,
            "reasoning": f"Direct momentum signal (window={best_window}): momentum={momentum:.4f}, quality={trend_quality:.4f} - No conservative smoothing applied",
            "market_regime": regime,
            "predicted_return": momentum * 1.0,  # Direct momentum scaling
            "risk_estimate": min(0.9, volatility * 2),
            "key_factors": key_factors,
            "execution_weight": confidence,  # Use full confidence as execution weight
            "analysis_source": "fallback_direct",
            "selected_timeframe": best_window,
            "timeframe_analysis": timeframe_analysis
        }

    def _calculate_momentum(self, prices: List[float], window: Optional[int] = None) -> float:
        """Calculate momentum indicator with intelligent window selection."""
        if window is None:
            window = self.config.strategy.window
            
        if len(prices) < window:
            return 0.0
        
        current_price = prices[-1]
        past_price = prices[-window]
        return (current_price - past_price) / past_price if past_price != 0 else 0.0

    def _calculate_volatility(self, prices: List[float]) -> float:
        """Calculate price volatility."""
        if len(prices) < 2:
            return 0.0
        
        returns = [(prices[i] - prices[i-1]) / prices[i-1] for i in range(1, len(prices)) if prices[i-1] != 0]
        if not returns:
            return 0.0
        
        mean_return = sum(returns) / len(returns)
        variance = sum((r - mean_return) ** 2 for r in returns) / len(returns)
        return variance ** 0.5

    def _read_memory(self, key: str):
        """Read a value from the shared memory unit."""
        if not os.path.exists(self.memory_path):
            return None
        try:
            with open(self.memory_path, 'r') as f:
                memory = json.load(f)
            return memory.get(key)
        except Exception as e:
            logger.warning(f"Failed to read memory: {e}")
            return None

    def _write_signal_flow(self, flow_data: dict):
        """Write strategy signal flow to a JSON file."""
        try:
            with open(self.signal_flow_path, 'w') as f:
                json.dump(flow_data, f, indent=2)
            logger.info(f"Signal flow written to {self.signal_flow_path}")
        except Exception as e:
            logger.warning(f"Failed to write signal flow: {e}")

    def _register_tools(self):
        """
        Register the agent's tools with the FastMCP server with LLM-powered analysis.
        """
        @self.agent.tool()
        async def generate_signal(symbol: str, price_list: Optional[List[float]] = None, ctx: MCPContext = None) -> dict:
            """
            Generate a sophisticated alpha strategy flow using LLM analysis and real market data.
            """
            request_id = f"{symbol}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
            logger.info(f"[REQUEST {request_id}] generate_signal called with symbol={symbol}, price_list_length={len(price_list) if price_list else 0}")
            
            try:
                # Convert parameters to MomentumSignalRequest object for internal processing
                try:
                    request = MomentumSignalRequest(symbol=symbol, price_list=price_list)
                    logger.info(f"[REQUEST {request_id}] Successfully created MomentumSignalRequest")
                except Exception as e:
                    logger.error(f"[REQUEST {request_id}] Error creating MomentumSignalRequest: {e}", exc_info=True)
                    return {
                        "signal": "HOLD",
                        "confidence": 0.0,
                        "error": f"Invalid request parameters: {e}",
                        "reasoning": f"Failed to parse request: {e}",
                        "request_id": request_id
                    }

                symbol = request.symbol
                price_list = request.price_list

                # Get price data
                logger.info(f"[REQUEST {request_id}] Getting price data for {symbol}")
                if price_list is None:
                    closes = []
                    for i in range(self.config.strategy.window):
                        key = f"{symbol}_close_2024-01-{str(31-i).zfill(2)} 05:00:00"
                        val = self._read_memory(key)
                        if val is not None:
                            closes.insert(0, float(val))
                    prices = closes if closes else self._generate_synthetic_prices(symbol, self.config.strategy.window)
                    logger.info(f"[REQUEST {request_id}] Using {'memory' if closes else 'synthetic'} price data: {len(prices)} points")
                else:
                    prices = price_list
                    logger.info(f"[REQUEST {request_id}] Using provided price data: {len(prices)} points")

                # Use LLM for intelligent analysis
                market_context_data = {
                    "symbol": symbol,
                    "timestamp": datetime.now().isoformat(),
                    "price_count": len(prices)
                }

                logger.info(f"[REQUEST {request_id}] Starting LLM analysis for {symbol}")
                analysis_result = await self._analyze_market_with_llm(symbol, prices, market_context_data)
                logger.info(f"[REQUEST {request_id}] LLM analysis result: {analysis_result}")

                # Build comprehensive output using LLM insights
                now = datetime.utcnow().replace(microsecond=0).isoformat() + "Z"
                code_hash = hashlib.sha256((str(prices) + analysis_result["signal"]).encode()).hexdigest()

                # Determine market regime
                regime_map = {
                    "BUY": analysis_result.get("market_regime", "bullish_trend"),
                    "SELL": analysis_result.get("market_regime", "bearish_trend"),
                    "HOLD": analysis_result.get("market_regime", "neutral")
                }

                flow_obj = AlphaStrategyFlow(
                    alpha_id="momentum_llm_v4",
                    version="2025.06.29-llm",
                    timestamp=now,
                    market_context=MarketContext(
                        symbol=symbol,
                        regime_tag=regime_map[analysis_result["signal"]],
                        input_features={
                            "price_current": prices[-1] if prices else 0,
                            "price_20d_ago": prices[0] if len(prices) >= 20 else (prices[0] if prices else 0),
                            "sma_10": sum(prices[-10:]) / min(10, len(prices)) if prices else 0,
                            "sma_20": sum(prices[-20:]) / min(20, len(prices)) if prices else 0,
                            "momentum_score": self._calculate_momentum(prices),
                            "volatility": self._calculate_volatility(prices),
                            "analysis_source": analysis_result.get("analysis_source", "unknown"),
                            "llm_factors": analysis_result.get("key_factors", [])
                        }
                    ),
                    decision=Decision(
                        signal=analysis_result["signal"],
                        confidence=analysis_result["confidence"],
                        reasoning=analysis_result["reasoning"],
                        predicted_return=analysis_result["predicted_return"],
                        risk_estimate=analysis_result["risk_estimate"],
                        signal_type="directional",
                        asset_scope=[symbol]
                    ),
                    action=Action(
                        execution_weight=analysis_result["execution_weight"],
                        order_type="market",
                        order_price=prices[-1] if prices else 0,
                        execution_delay="T+0"
                    ),
                    performance_feedback=PerformanceFeedback(
                        status="pending",
                        evaluation_link=None
                    ),
                    metadata=Metadata(
                        generator_agent="momentum_llm_agent",
                        strategy_prompt="LLM-powered momentum analysis with sophisticated market regime detection",
                        code_hash=f"sha256:{code_hash}",
                        context_id=f"llm_dag_{now[:10].replace('-', '')}_{now[11:13]}"
                    )
                )

                # Write to strategy flow file
                try:
                    # Use model_dump() for newer Pydantic versions, fallback to dict() for older versions
                    if hasattr(flow_obj, 'model_dump'):
                        flow_dict = flow_obj.model_dump()
                    else:
                        flow_dict = flow_obj.dict()
                    self._write_signal_flow(flow_dict)
                except Exception as e:
                    logger.warning(f"Failed to write signal flow: {e}")

                try:
                    # Use model_dump() for newer Pydantic versions, fallback to dict() for older versions
                    if hasattr(flow_obj, 'model_dump'):
                        result = flow_obj.model_dump()
                    else:
                        result = flow_obj.dict()
                    logger.info(f"[REQUEST {request_id}] Successfully generated signal for {symbol}: {result.get('decision', {}).get('signal', 'UNKNOWN')}")
                    return result
                except Exception as e:
                    logger.error(f"[REQUEST {request_id}] Error converting flow object to dict: {e}", exc_info=True)
                    # Return a simplified response if the full object fails
                    return {
                        "signal": analysis_result.get("signal", "HOLD"),
                        "confidence": analysis_result.get("confidence", 0.0),
                        "reasoning": analysis_result.get("reasoning", "Error converting response"),
                        "error": str(e),
                        "request_id": request_id
                    }
            except Exception as e:
                logger.error(f"[REQUEST {request_id}] Error executing tool generate_signal: {e}", exc_info=True)
                return {
                    "signal": "HOLD",
                    "confidence": 0.0,
                    "raw_response": f"Error executing tool generate_signal: {e}",
                    "request_id": request_id
                }

        @self.agent.tool()
        async def analyze_market_sentiment(symbol: str, lookback_days: int = 20) -> dict:
            """
            Analyze market sentiment using LLM for the given symbol.
            """
            try:
                # Get price data
                prices = []
                for i in range(lookback_days):
                    key = f"{symbol}_close_2024-01-{str(31-i).zfill(2)} 05:00:00"
                    val = self._read_memory(key)
                    if val is not None:
                        prices.insert(0, float(val))
                
                if not prices:
                    prices = self._generate_synthetic_prices(symbol, lookback_days)
                
                # Use LLM for sentiment analysis
                analysis = await self._analyze_market_with_llm(symbol, prices, {})
                
                return {
                    "symbol": symbol,
                    "sentiment": analysis.get("market_regime", "neutral"),
                    "confidence": analysis.get("confidence", 0.0),
                    "key_factors": analysis.get("key_factors", []),
                    "reasoning": analysis.get("reasoning", ""),
                    "timestamp": datetime.now().isoformat(),
                    "analysis_source": analysis.get("analysis_source", "llm")
                }
                
            except Exception as e:
                return {
                    "symbol": symbol,
                    "sentiment": "unknown",
                    "confidence": 0.0,
                    "error": str(e),
                    "timestamp": datetime.now().isoformat()
                }

    def _generate_synthetic_prices(self, symbol: str, window: int) -> List[float]:
        """
        Generate realistic synthetic price series for testing when real data is unavailable.
        """
        import random
        base_price = 100.0
        prices = [base_price]
        
        for _ in range(window - 1):
            # Simple random walk with slight upward bias
            change = random.gauss(0.001, 0.02)  # 0.1% daily drift, 2% volatility
            new_price = prices[-1] * (1 + change)
            prices.append(max(new_price, 1.0))  # Ensure price stays positive
        
        return prices

    def start_mcp_server(self, host="0.0.0.0", port: Optional[int] = None, use_sse: bool = True):
        """
        Start the MCP server for the agent, supporting SSE communication by default.
        Args:
            host (str): The host to bind the server to.
            port (int, optional): The port to run the server on. Defaults to config.
            use_sse (bool): Whether to use SSE (Server-Sent Events) for communication. Default is True.
        """
        port = port or self.config.execution.port
        self.agent.settings.host = host
        self.agent.settings.port = port
        
        # Choose transport based on use_sse parameter
        transport = "sse" if use_sse else "stdio"
        
        logger.info(f"Starting LLM-powered MomentumAgent MCP server on {host}:{port} with transport: {transport}")
        if self.llm_client:
            logger.info("🤖 LLM integration enabled - using intelligent analysis")
        else:
            logger.warning("⚠️ LLM not available - using fallback analysis")

        self.agent.run(transport=transport)

    def __repr__(self):
        """
        Return a string representation of the enhanced MomentumAgent.
        """
        llm_status = "LLM-enabled" if self.llm_client else "fallback-mode"
        return f"<MomentumAgent id={self.config.agent_id} port={self.config.execution.port} {llm_status}>"


def main(config_dict=None):
    import os
    import sys
    import yaml
    
    def to_dict_recursive(obj):
        """
        Recursively convert an object to a dict, compatible with pydantic, dataclass, and normal objects.
        """
        if isinstance(obj, dict):
            return {k: to_dict_recursive(v) for k, v in obj.items()}
        if hasattr(obj, 'model_dump'):
            return to_dict_recursive(obj.model_dump())
        if hasattr(obj, '__dict__') and not isinstance(obj, type):
            return to_dict_recursive(vars(obj))
        if isinstance(obj, (list, tuple, set)):
            return [to_dict_recursive(i) for i in obj]
        return obj

    if config_dict is not None:
        # If config_dict is provided, use it to initialize the agent and start the MCP server.
        config_dict = to_dict_recursive(config_dict)
        config = MomentumAgentConfig(**config_dict)
        agent = MomentumAgent(config)
        agent.start_mcp_server()
        return
    
    import argparse
    parser = argparse.ArgumentParser(description="Start MomentumAgent MCP server.")
    parser.add_argument('--sse', action='store_true', help='Use SSE channel (default, reserved for future extension)')
    args = parser.parse_args()

    # Load config from YAML file if no config_dict is provided
    config_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "../../config/momentum.yaml"))
    if not os.path.exists(config_path):
        print(f"[ERROR] Config file not found: {config_path}")
        sys.exit(1)
    with open(config_path, 'r') as f:
        config_data = yaml.safe_load(f)
    config = MomentumAgentConfig(**config_data)
    agent = MomentumAgent(config)
    # Default to SSE channel; parameter reserved for future use
    agent.start_mcp_server()


if __name__ == "__main__":
    main()
