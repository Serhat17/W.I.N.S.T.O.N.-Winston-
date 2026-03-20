/* ════════════════════════════════════════════════════════
   W.I.N.S.T.O.N. Landing Page — script.js
   Terminal typing, demo chat, tabs, FAQ, navbar
   ════════════════════════════════════════════════════════ */

// ─── Navbar scroll ───
const navbar = document.getElementById('navbar');
window.addEventListener('scroll', () => {
  navbar.classList.toggle('scrolled', window.scrollY > 20);
});

// ─── Mobile nav toggle ───
const navToggle = document.getElementById('navToggle');
const navLinks = document.getElementById('navLinks');
navToggle.addEventListener('click', () => {
  navLinks.classList.toggle('open');
  const spans = navToggle.querySelectorAll('span');
  if (navLinks.classList.contains('open')) {
    spans[0].style.transform = 'rotate(45deg) translate(5px, 5px)';
    spans[1].style.opacity = '0';
    spans[2].style.transform = 'rotate(-45deg) translate(5px, -5px)';
  } else {
    spans[0].style.transform = '';
    spans[1].style.opacity = '';
    spans[2].style.transform = '';
  }
});

// Close nav on link click
navLinks.querySelectorAll('a').forEach(link => {
  link.addEventListener('click', () => {
    navLinks.classList.remove('open');
    const spans = navToggle.querySelectorAll('span');
    spans.forEach(s => { s.style.transform = ''; s.style.opacity = ''; });
  });
});

// ─── Hero terminal typing animation ───
const conversations = [
  {
    cmd: 'curl -fsSL https://...install.sh | bash',
    output: `<span class="out-check">✓</span> Python 3.12 installed
<span class="out-check">✓</span> Ollama installed &amp; model pulled
<span class="out-check">✓</span> Winston installed. Type <span class="out-winston">winston</span> to start.`
  },
  {
    cmd: 'winston',
    output: `<span class="out-user">You:</span> Schick eine Mail an max — Betreff: Projekt Update
<span class="out-winston">Winston:</span> <span class="out-check">✓</span> Email gesendet an max@example.com`
  },
  {
    cmd: 'winston voice',
    output: `<span class="out-check">●</span> Listening for wake word...
<span class="out-user">You:</span> "Hey Winston, what's on my calendar tomorrow?"
<span class="out-winston">Winston:</span> Tomorrow: 10:00 Team Standup, 14:30 Dentist`
  },
  {
    cmd: 'winston server',
    output: `<span class="out-check">✓</span> Web UI → http://localhost:8000
<span class="out-check">✓</span> Telegram connected — @WinstonBot
<span class="out-check">✓</span> 22 skills loaded, 3 channels active`
  }
];

const cmdEl = document.getElementById('typingCmd');
const outputEl = document.getElementById('terminalOutput');
let convIdx = 0;

function typeCommand(text, idx, cb) {
  if (idx <= text.length) {
    cmdEl.textContent = text.slice(0, idx);
    setTimeout(() => typeCommand(text, idx + 1, cb), 35 + Math.random() * 25);
  } else {
    cb();
  }
}

function showConversation() {
  const conv = conversations[convIdx];
  cmdEl.textContent = '';
  outputEl.innerHTML = '';
  
  typeCommand(conv.cmd, 0, () => {
    setTimeout(() => {
      outputEl.innerHTML = conv.output;
      setTimeout(() => {
        convIdx = (convIdx + 1) % conversations.length;
        showConversation();
      }, 4000);
    }, 400);
  });
}

// Start after a short delay
setTimeout(showConversation, 800);

// ─── Demo chat messages ───
const demoConversations = [
  [
    { role: 'user', text: 'Watch the price of the Sony WH-1000XM5 on Amazon — alert me under 250€' },
    { role: 'bot', text: '<span class="msg-check">✓</span> Now monitoring Sony WH-1000XM5. I\'ll alert you when the price drops below €250.' },
    { role: 'user', text: 'Run this Python code: print(sum(range(1, 101)))' },
    { role: 'bot', text: 'Output: <strong>5050</strong>' },
    { role: 'user', text: 'Generate an image of a futuristic Tokyo at sunset' },
    { role: 'bot', text: '<span class="msg-check">✓</span> Image generated and saved. Neon-lit streets with Mount Fuji in the background.' },
  ],
  [
    { role: 'user', text: 'Fass mir meine Mails zusammen' },
    { role: 'bot', text: '3 neue Mails:<br>1. Max — Projekt Update (Action Required)<br>2. Amazon — Bestellbestätigung #304<br>3. Newsletter — KI-News der Woche' },
    { role: 'user', text: 'Trag morgen um 10 einen Termin "Team Sync" ein' },
    { role: 'bot', text: '<span class="msg-check">✓</span> Termin "Team Sync" für morgen 10:00 erstellt.' },
    { role: 'user', text: 'Bestell 2 Milch und Brot bei Flink' },
    { role: 'bot', text: '<span class="msg-check">✓</span> 3 Produkte im Warenkorb bei Flink (4,63€). Soll ich bestellen?' },
  ],
  [
    { role: 'user', text: 'What flights are available from Berlin to Barcelona next weekend?' },
    { role: 'bot', text: 'Found 4 flights:<br>• Ryanair FR 123 — €45, 07:20–10:15<br>• easyJet U2 456 — €62, 11:00–14:00<br>• Vueling VY 789 — €78, 15:30–18:25<br>• Lufthansa LH 012 — €149, 09:00–12:10' },
    { role: 'user', text: '/daily on' },
    { role: 'bot', text: '<span class="msg-check">✓</span> Morning briefing enabled. I\'ll send you weather, calendar, and news every day at 07:30.' },
  ]
];

const messagesEl = document.getElementById('demoMessages');
let demoIdx = 0;

function showDemoConversation() {
  const msgs = demoConversations[demoIdx];
  messagesEl.innerHTML = '';
  
  msgs.forEach((msg, i) => {
    setTimeout(() => {
      const div = document.createElement('div');
      div.className = `demo-msg ${msg.role === 'user' ? 'user' : 'bot'}`;
      div.innerHTML = msg.text;
      messagesEl.appendChild(div);
      messagesEl.scrollTop = messagesEl.scrollHeight;
    }, i * 700);
  });
  
  setTimeout(() => {
    demoIdx = (demoIdx + 1) % demoConversations.length;
    showDemoConversation();
  }, msgs.length * 700 + 4000);
}

setTimeout(showDemoConversation, 2000);

// ─── Install tabs ───
document.querySelectorAll('.install-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.install-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.install-panel').forEach(p => p.classList.remove('active'));
    tab.classList.add('active');
    const panel = document.getElementById('tab-' + tab.dataset.tab);
    if (panel) panel.classList.add('active');
  });
});

// ─── FAQ toggle ───
function toggleFaq(btn) {
  const item = btn.closest('.faq-item');
  const wasOpen = item.classList.contains('open');
  // Close all
  document.querySelectorAll('.faq-item').forEach(i => i.classList.remove('open'));
  // Toggle clicked
  if (!wasOpen) item.classList.add('open');
}

// ─── Copy code ───
function copyCode(btn) {
  const block = btn.closest('.code-block') || btn.closest('.terminal-code');
  const code = block.querySelector('pre code, pre');
  const text = code ? code.textContent : '';
  
  navigator.clipboard.writeText(text).then(() => {
    btn.textContent = 'Copied!';
    btn.style.color = 'var(--green)';
    btn.style.borderColor = 'var(--green)';
    setTimeout(() => {
      btn.textContent = 'Copy';
      btn.style.color = '';
      btn.style.borderColor = '';
    }, 2000);
  });
}

// ─── Smooth scroll for anchor links ───
document.querySelectorAll('a[href^="#"]').forEach(link => {
  link.addEventListener('click', (e) => {
    const target = document.querySelector(link.getAttribute('href'));
    if (target) {
      e.preventDefault();
      const offset = 80;
      const top = target.getBoundingClientRect().top + window.scrollY - offset;
      window.scrollTo({ top, behavior: 'smooth' });
    }
  });
});

// ─── Intersection Observer for fade-in animations ───
const observerOptions = { threshold: 0.1, rootMargin: '0px 0px -40px 0px' };
const fadeObserver = new IntersectionObserver((entries) => {
  entries.forEach(entry => {
    if (entry.isIntersecting) {
      entry.target.style.opacity = '1';
      entry.target.style.transform = 'translateY(0)';
      fadeObserver.unobserve(entry.target);
    }
  });
}, observerOptions);

// Apply to section elements
document.querySelectorAll('.feature-card, .skill-card, .channel-card, .faq-item, .arch-layer').forEach(el => {
  el.style.opacity = '0';
  el.style.transform = 'translateY(20px)';
  el.style.transition = 'opacity 0.5s ease, transform 0.5s ease';
  fadeObserver.observe(el);
});
