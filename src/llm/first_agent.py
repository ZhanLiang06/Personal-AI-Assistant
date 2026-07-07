import os
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from langchain_core.tools import tool
from langchain_core.messages import HumanMessage,ToolMessage

load_dotenv()

@tool
def get_stock_price(ticker : str) -> str:
    """Look up the current price of a stock given its ticker symbol."""
    fake_prices = {"AAPL": 195.20, "NVDA": 118.50}  # placeholder data for now
    price = fake_prices.get(ticker.upper())
    if price is None:
        return f"No price data found for {ticker}"
    return f"{ticker.upper()} is currently trading at ${price}"

llm = ChatGroq(model="llama-3.3-70b-versatile",temperature=0)

llm_with_tools = llm.bind_tools([get_stock_price])

history = [HumanMessage(content="What is the current price of NVDA?")]

response = llm_with_tools.invoke(history)
history.append(response)

print("Did the model request a tool call?", bool(response.tool_calls))
print("Tool call details:", response.tool_calls)
print()

if response.tool_calls:
    for call in response.tool_calls:
        result = get_stock_price.invoke(call["args"])
        history.append(ToolMessage(content=result, tool_call_id=call["id"]))

    final_response = llm_with_tools.invoke(history)
    print("Final answer:", final_response.content)
else:
    print("fianl answer:", response.content)

print(history)