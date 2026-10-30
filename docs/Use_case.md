# Project 2: Vendor Sales and Order AI Agent

## 1. Project Overview

Build an AI sales assistant for a small Nigerian online business. The agent will communicate with customers through a simple, friendly, WhatsApp-style chat.

It should answer questions about products, help customers place orders, negotiate prices, provide payment links, and track orders.

The goal is to **reduce the vendor's workload, prevent order mistakes, and improve customer response time.**

The agent does not need to connect to WhatsApp. It will be tested on Fleet.

## 2. Business Setup

Choose one type of business:

- Fashion and clothing
- Food and small chops
- Cosmetics and skincare
- Phones and gadgets

Create a fictional business with **20–30 products**, including their prices, available quantities, descriptions, and variations.

## 3. What the Agent Should Do

**1. Answer customer questions**

Provide information about product prices, sizes, colours, availability, ingredients, and specifications.

**2. Handle deliveries**

Calculate delivery fees based on the customer's location and explain how long delivery will take.

**3. Take customer orders**

Guide customers through selecting products, choosing quantities, and providing their delivery address and phone number.

Before placing an order, show the customer a complete summary, including the total cost, and ask for confirmation.

**4. Handle payments**

Generate a mock Paystack or Flutterwave payment link after an order is created. Customers must never provide their card details in the chat.

**5. Negotiate prices**

Allow customers to request discounts, but only within the limits set by the business owner.

**6. Explain business policies**

Answer questions about returns, exchanges, payments, and delivery.

**7. Handle difficult situations**

Notify the business owner when customers have complaints, special requests, or problems the agent cannot resolve.

## 4. Tools to Build

The agent will use nine tools to perform its tasks.

| Tool | Purpose |
|---|---|
| `search_catalogue` | Search for products by name, category, or price |
| `check_stock` | Check available product quantities and variants |
| `get_delivery_fee` | Calculate delivery cost and estimated time |
| `apply_discount` | Apply discounts within the allowed limit |
| `create_order` | Save an order after customer confirmation |
| `create_payment_link` | Generate a mock payment link |
| `get_order_status` | Check payment and delivery progress |
| `notify_owner` | Inform the owner about issues requiring attention |

**Important rules:**

- Only `create_order` requires explicit customer confirmation.
- Prices and stock quantities must come from tools, not the AI's memory.
- The discount tool must reject discounts above the owner's limit.
- The order tool must reject orders when stock is insufficient.
- Customers must pay through a payment link, not by sharing card details in chat.

## 5. Knowledge Base (RAG)

Create 5–6 short documents containing information the agent can retrieve when answering customer questions.

These documents should cover:

1. **Product information:** Descriptions, sizing or ingredient guides, and care instructions.
2. **Delivery policy:** Delivery locations, charges, timelines, and order cut-off times.
3. **Returns and exchanges:** Conditions for returning or exchanging products.
4. **Payment and order policy:** Accepted payment methods, order confirmation, and unpaid orders.
5. **Negotiation rules:** Maximum discounts, bulk discounts, and products that cannot be discounted.
6. **Communication guide and FAQ:** How the agent should speak to customers, plus 20 common customer questions and answers.

The agent should use RAG to retrieve this information and cite the relevant policy when necessary.

## 6. Demo Scenarios

The completed agent must successfully handle seven situations.

**Scenario 1: Product enquiry**

Customer asks about the price and availability of a particular product, including its size or variation.

The agent searches the catalogue and checks stock.

**Scenario 2: Placing an order**

Customer requests two products for delivery to Lekki.

The agent calculates delivery fees, displays the total, asks for confirmation, creates the order, and sends a payment link.

**Scenario 3: Price negotiation**

Customer says, "Abeg reduce the price small."

The agent offers an allowed discount or politely refuses if the request exceeds the limit.

**Scenario 4: Order tracking**

Customer asks, "Where is my order?"

The agent retrieves and explains the current order status.

**Scenario 5: Returns and exchanges**

Customer asks what happens if a product does not fit or meet expectations.

The agent explains the return policy and cites its source.

**Scenario 6: Out-of-stock product**

When a requested product is unavailable, the agent suggests similar products that are currently in stock.

**Scenario 7: Order modification**

A customer changes their selected product or delivery address before confirming the order.

The agent updates the existing order details without restarting the conversation.

## 7. Optional Advanced Features

**Owner Mode**

Allow the business owner to interact with the same agent to ask questions such as:

- "How many orders did we receive today?"
- "Which products should I restock this week?"

For restocking recommendations, the agent should use a simple demand forecasting model through a tool.

**Voice Note Support**

Allow customers to send voice messages.

A speech-to-text model such as Whisper will convert the audio into text so the agent can understand and respond.

## 8. Expected Outcome

At the end of the project, there should be a working AI sales agent that can:

- Communicate naturally with customers.
- Retrieve accurate product and business information.
- Manage orders from enquiry to payment.
- Negotiate prices safely.
- Track orders and handle changes.
- Escalate problems to the business owner.
- Follow business rules through code, not just prompts.

**The final goal is to build a reliable virtual sales assistant that handles everyday customer interactions while allowing the business owner to focus on important decisions.**