import React, { useEffect, useState } from 'react';
import { createRoot } from 'react-dom/client';
import './style.css';

const API = import.meta.env.VITE_API_URL || '';

function App() {
  const [tab, setTab] = useState('Catalogue');
  const [products, setProducts] = useState([]);
  const [search, setSearch] = useState('');
  const [cart, setCart] = useState({});
  const [dealers, setDealers] = useState([]);
  const [dealer, setDealer] = useState('');
  const [orders, setOrders] = useState([]);
  const [chat, setChat] = useState([]);
  const [message, setMessage] = useState('');
  const [forecast, setForecast] = useState([]);
  const [error, setError] = useState('');

  async function api(path, opts = {}) {
    const response = await fetch(API + path, {
      headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
      ...opts,
    });
    const body = await response.json();
    if (!response.ok) throw Error(body.error?.message || 'Request failed');
    return body;
  }

  async function load() {
    try {
      const productData = await api('/api/products/?page_size=100');
      setProducts(productData.results);
      const dealerData = await api('/api/dealers/?page_size=100');
      setDealers(dealerData.results);
      if (!dealer && dealerData.results[0]) setDealer(String(dealerData.results[0].id));
      const orderData = await api('/api/orders/?page_size=100');
      setOrders(orderData.results);
      const forecastData = await api('/api/reorder-suggestions/');
      setForecast(forecastData.results);
    } catch (requestError) {
      setError(requestError.message + ' — check that the Django API is running.');
    }
  }

  useEffect(() => {
    load();
  }, []);

  const filtered = products.filter((product) =>
    (product.name + ' ' + product.sku + ' ' + product.brand + ' ' + product.vehicle_fitment)
      .toLowerCase()
      .includes(search.toLowerCase()),
  );

  async function makeOrder() {
    try {
      const items = Object.entries(cart)
        .filter(([_, quantity]) => quantity > 0)
        .map(([sku, quantity]) => ({ sku, quantity: Number(quantity) }));
      await api('/api/orders/', {
        method: 'POST',
        body: JSON.stringify({ dealer_id: Number(dealer), items }),
      });
      setCart({});
      setError('Draft order created successfully.');
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function sendChat() {
    if (!message.trim()) return;
    const currentMessage = message;
    setChat((currentChat) => [...currentChat, { role: 'dealer', text: currentMessage }]);
    setMessage('');
    try {
      const response = await api('/api/assistant/chat/', {
        method: 'POST',
        body: JSON.stringify({
          session_id: 'web-session',
          dealer_id: Number(dealer),
          message: currentMessage,
        }),
      });
      setChat((currentChat) => [
        ...currentChat,
        {
          role: 'assistant',
          text: response.reply,
          tools: response.tool_calls,
          assistantMode: response.assistant_mode,
          providerNotice: response.provider_notice,
        },
      ]);
      await load();
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  async function action(orderNumber, actionName) {
    try {
      await api(`/api/orders/${orderNumber}/${actionName}/`, {
        method: 'POST',
        body: '{}',
      });
      await load();
      setError('Order updated.');
    } catch (requestError) {
      setError(requestError.message);
    }
  }

  return (
    <div className="app">
      <aside>
        <div className="brand">
          VIKMO<span>DealerDesk</span>
        </div>
        <div className="menu">
          {['Catalogue', 'Orders', 'AI Assistant', 'Reorder Planning'].map((item) => (
            <button
              key={item}
              className={tab === item ? 'active' : ''}
              onClick={() => setTab(item)}
            >
              {item}
            </button>
          ))}
        </div>
        <div className="sidefoot">
          Dealer commerce workspace
          <br />
          API: {API}
        </div>
      </aside>
      <main>
        <header>
          <div>
            <small>DEALER PORTAL</small>
            <h1>{tab}</h1>
          </div>
          <label className="dealerSelector">
            <small>Active dealer · used for new orders</small>
            <select
              aria-label="Active dealer for new orders"
              value={dealer}
              onChange={(event) => setDealer(event.target.value)}
            >
              {dealers.map((item) => (
                <option key={item.id} value={item.id}>
                  {item.name} · {item.dealer_code}
                </option>
              ))}
            </select>
          </label>
        </header>
        {error && (
          <div className="notice" onClick={() => setError('')}>
            {error} <b>×</b>
          </div>
        )}

        {tab === 'Catalogue' && (
          <>
            <div className="toolbar">
              <input
                placeholder="Search parts, SKU, brand or vehicle…"
                value={search}
                onChange={(event) => setSearch(event.target.value)}
              />
              <div>{filtered.length} products loaded</div>
            </div>
            <div className="layout">
              <section className="products">
                {filtered.map((product) => (
                  <article className="product" key={product.sku}>
                    <div className="tag">{product.category}</div>
                    <h3>{product.name}</h3>
                    <p>
                      {product.brand} · {product.vehicle_fitment}
                    </p>
                    <small>{product.sku}</small>
                    <div className="productbottom">
                      <strong>₹{product.price}</strong>
                      <span className={product.stock < 5 ? 'low' : 'stock'}>
                        {product.stock} in stock
                      </span>
                    </div>
                    <div className="add">
                      <button
                        onClick={() =>
                          setCart((currentCart) => ({
                            ...currentCart,
                            [product.sku]: Math.max(0, (currentCart[product.sku] || 0) - 1),
                          }))
                        }
                      >
                        −
                      </button>
                      <span>{cart[product.sku] || 0}</span>
                      <button
                        disabled={
                          product.stock === 0 || (cart[product.sku] || 0) >= product.stock
                        }
                        onClick={() =>
                          setCart((currentCart) => ({
                            ...currentCart,
                            [product.sku]: (currentCart[product.sku] || 0) + 1,
                          }))
                        }
                      >
                        +
                      </button>
                    </div>
                  </article>
                ))}
              </section>
              <section className="cart">
                <h2>Draft order</h2>
                <p className="muted">
                  Creating a draft does not change stock. Confirm it in Orders to deduct stock.
                </p>
                {Object.entries(cart)
                  .filter(([_, quantity]) => quantity > 0)
                  .map(([sku, quantity]) => {
                    const product = products.find((item) => item.sku === sku);
                    return (
                      <div className="cartline" key={sku}>
                        <span>
                          {product?.name} × {quantity}
                        </span>
                        <b>₹{(Number(product?.price || 0) * quantity).toFixed(2)}</b>
                      </div>
                    );
                  })}
                <div className="total">
                  <span>Subtotal</span>
                  <b>
                    ₹
                    {Object.entries(cart)
                      .reduce(
                        (sum, [sku, quantity]) =>
                          sum + Number(products.find((item) => item.sku === sku)?.price || 0) * quantity,
                        0,
                      )
                      .toFixed(2)}
                  </b>
                </div>
                <button
                  className="primary full"
                  disabled={!Object.values(cart).some((quantity) => quantity > 0) || !dealer}
                  onClick={makeOrder}
                >
                  Create draft order
                </button>
              </section>
            </div>
          </>
        )}

        {tab === 'Orders' && (
          <div className="tablewrap">
            <table>
              <thead>
                <tr>
                  <th>Order</th>
                  <th>Dealer ID</th>
                  <th>Status</th>
                  <th>Total incl. GST</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {orders.map((order) => (
                  <tr key={order.order_number}>
                    <td>{order.order_number}</td>
                    <td>{order.dealer_id}</td>
                    <td>
                      <span className="pill">{order.status}</span>
                    </td>
                    <td>₹{order.grand_total}</td>
                    <td>
                      {order.status === 'DRAFT' && (
                        <button onClick={() => action(order.order_number, 'confirm')}>Confirm</button>
                      )}
                      {order.status === 'CONFIRMED' && (
                        <button onClick={() => action(order.order_number, 'dispatch')}>Dispatch</button>
                      )}
                      {order.status === 'DISPATCHED' && (
                        <button onClick={() => action(order.order_number, 'deliver')}>Deliver</button>
                      )}
                      {['DRAFT', 'CONFIRMED'].includes(order.status) && (
                        <button onClick={() => action(order.order_number, 'cancel')}>Cancel</button>
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        {tab === 'AI Assistant' && (
          <div className="chatpanel">
            <div className="chathead">
              <div className="orb">✦</div>
              <div>
                <h2>DealerDesk AI</h2>
                <p className="muted">Product discovery · Live inventory · Draft orders</p>
              </div>
            </div>
            <div className="messages">
              {!chat.length && (
                <div className="welcome">
                  <h2>What parts do you need?</h2>
                  <p>Ask about fitment, availability, or preparing a draft order.</p>
                  <button onClick={() => setMessage('Find brake pads for a KTM Duke 390')}>
                    Find brake pads for a KTM Duke 390
                  </button>
                </div>
              )}
              {chat.map((item, index) => (
                <div className={'bubble ' + item.role} key={`${item.role}-${index}`}>
                  <p>{item.text}</p>
                  {item.providerNotice && <small className="providerNotice">{item.providerNotice}</small>}
                  {item.tools?.length > 0 && (
                    <small>
                      {item.assistantMode === 'gemini' ? 'Gemini tools: ' : 'Database lookups: '}
                      {item.tools.map((tool) => tool.name).join(' → ')}
                    </small>
                  )}
                </div>
              ))}
            </div>
            <div className="chatinput">
              <input
                value={message}
                onChange={(event) => setMessage(event.target.value)}
                onKeyDown={(event) => event.key === 'Enter' && sendChat()}
                placeholder="Ask about a part or vehicle…"
              />
              <button className="primary" onClick={sendChat}>
                Send ↑
              </button>
            </div>
          </div>
        )}

        {tab === 'Reorder Planning' && (
          <>
            <p className="muted">
              Recommendations use the committed 4-week forecast and live inventory quantities.
            </p>
            <div className="tablewrap">
              <table>
                <thead>
                  <tr>
                    <th>SKU</th>
                    <th>Current stock</th>
                    <th>Lead time</th>
                    <th>Lead-time demand</th>
                    <th>Safety stock</th>
                    <th>Reorder point</th>
                    <th>Suggested qty</th>
                  </tr>
                </thead>
                <tbody>
                  {forecast.map((item) => (
                    <tr key={item.sku}>
                      <td>{item.sku}</td>
                      <td>{item.current_stock}</td>
                      <td>{item.lead_time_weeks} weeks</td>
                      <td>{item.lead_time_demand}</td>
                      <td>{item.safety_stock}</td>
                      <td>{item.reorder_point}</td>
                      <td>
                        <b className={item.reorder_qty ? 'low' : 'stock'}>{item.reorder_qty}</b>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </main>
    </div>
  );
}

createRoot(document.getElementById('root')).render(<App />);
