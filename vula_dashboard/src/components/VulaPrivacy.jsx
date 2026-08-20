/**
 * VulaPrivacy.jsx — Public privacy policy + terms page.
 * Required for Meta WhatsApp Business app publishing. POPIA-compliant (SA).
 * Rendered without auth at #/privacy and #/terms.
 */

const C = {
  bg: "#F7F4EE", surface: "#FFFFFF", border: "#DDD8CE",
  green: "var(--accent)", muted: "#8A8680", text: "#1E1E1E",
};

export default function VulaPrivacy({ view = "privacy" }) {
  return (
    <div style={{ minHeight: "100vh", background: C.bg, padding: "48px 24px" }}>
      <div style={{ maxWidth: 760, margin: "0 auto", background: C.surface,
        border: `1px solid ${C.border}`, borderRadius: 12, padding: 48 }}>
        <div style={{ marginBottom: 32 }}>
          <span style={{ fontSize: 28, fontWeight: 700, color: C.green }}>Vula</span>
          <span style={{ fontSize: 12, color: C.muted, letterSpacing: "0.08em",
            textTransform: "uppercase", display: "block", marginTop: 2 }}>
            Vula Group (Pty) Ltd
          </span>
        </div>

        {view === "terms" ? <Terms /> : view === "data-deletion" ? <DataDeletion /> : <Privacy />}

        <div style={{ marginTop: 40, paddingTop: 20, borderTop: `1px solid ${C.border}`,
          fontSize: 13, color: C.muted }}>
          <a href="#/privacy" style={{ color: C.green, marginRight: 16 }}>Privacy Policy</a>
          <a href="#/terms" style={{ color: C.green, marginRight: 16 }}>Terms of Service</a>
          <a href="#/data-deletion" style={{ color: C.green }}>Data Deletion</a>
          <p style={{ marginTop: 12 }}>
            Vula Group (Pty) Ltd · Cape Town, South Africa · hello@vula.co.za
          </p>
        </div>
      </div>
    </div>
  );
}

function DataDeletion() {
  return (
    <div style={style.body}>
      <h1 style={style.h1}>User Data Deletion</h1>
      <p style={style.meta}>Last updated: 3 June 2026</p>

      <p style={style.p}>
        You have the right to request deletion of your personal data held by Vula at any
        time, in accordance with the Protection of Personal Information Act (POPIA).
      </p>

      <h2 style={style.h2}>How to request deletion</h2>
      <p style={style.p}>
        Send a deletion request using any of the following methods:
      </p>
      <ul style={style.ul}>
        <li>
          <strong>WhatsApp:</strong> Message the word <strong>DELETE</strong> to the Vula
          number you communicate with. We will confirm and remove your data.
        </li>
        <li>
          <strong>Email:</strong> Send a request to <strong>hello@vula.co.za</strong> with
          the subject "Data Deletion Request" and the phone number or email associated
          with your account.
        </li>
      </ul>

      <h2 style={style.h2}>What gets deleted</h2>
      <ul style={style.ul}>
        <li>Your WhatsApp number, name, and message history with Vula</li>
        <li>Documents you uploaded and the knowledge base built from them</li>
        <li>Any business records (orders, expenses, tasks) stored on your behalf</li>
        <li>
          For connected businesses: your WhatsApp Business Account credentials — disconnect
          in Settings → WhatsApp to revoke the access token immediately, or email us to remove it.
        </li>
      </ul>

      <h2 style={style.h2}>Timeframe</h2>
      <p style={style.p}>
        We process deletion requests within 30 days. Some information may be retained
        where required by South African law (for example, tax records), after which it is
        also deleted. We will confirm once your data has been removed.
      </p>

      <h2 style={style.h2}>Contact</h2>
      <p style={style.p}>
        Vula Group (Pty) Ltd, Cape Town, South Africa. Email: hello@vula.co.za
      </p>
    </div>
  );
}

function Privacy() {
  return (
    <div style={style.body}>
      <h1 style={style.h1}>Privacy Policy</h1>
      <p style={style.meta}>Last updated: 3 June 2026</p>

      <p style={style.p}>
        Vula Group (Pty) Ltd ("Vula", "we", "us") operates an AI business assistant
        delivered via WhatsApp and web. This policy explains what data we collect, how
        we use it, and your rights under the Protection of Personal Information Act
        (POPIA, Act 4 of 2013).
      </p>

      <h2 style={style.h2}>1. Information we collect</h2>
      <ul style={style.ul}>
        <li>Your WhatsApp phone number and display name</li>
        <li>Messages you send to Vula and our AI-generated responses</li>
        <li>Documents you upload (quotes, invoices, plans) to build your knowledge base</li>
        <li>Business records you choose to manage through Vula (orders, expenses, tasks)</li>
      </ul>

      <h2 style={style.h2}>2. How we use your information</h2>
      <ul style={style.ul}>
        <li>To answer your questions using your own business knowledge base</li>
        <li>To generate documents, quotes, and reports on your behalf</li>
        <li>To send you notifications about orders, tasks, and payments you request</li>
        <li>To improve the accuracy of responses for your business over time</li>
      </ul>

      <h2 style={style.h2}>3. WhatsApp Business Platform & connecting your account</h2>
      <p style={style.p}>
        Vula is a technology provider on the WhatsApp Business Platform (Meta). Businesses
        connect their own WhatsApp Business Account (WABA) to Vula through Meta's secure
        Embedded Signup flow. With the business's authorisation, we access and store only
        what is needed to operate their messaging: the WhatsApp Business Account ID, phone
        number ID, display name, and an access token. We use this solely to send and
        receive messages on that business's behalf and to register the message webhook.
      </p>
      <p style={style.p}>
        For a connected business, Vula processes the messages between that business and its
        customers to provide ordering, support, confirmations, and opt-in broadcasts. We act
        as a processor on the business's behalf for its customers' data. We do not use
        WhatsApp data for advertising, we do not sell it, and we do not share it with any
        third party except the sub-processors listed in section 5. Our use of the WhatsApp
        Business Platform complies with Meta's Platform Terms and the WhatsApp Business
        Messaging Policy. We do not send unsolicited marketing without opt-in, and recipients
        can stop messages at any time by replying STOP. A business can disconnect at any time
        (Settings → WhatsApp), which revokes the token and stops all messaging immediately.
      </p>

      <h2 style={style.h2}>4. Data storage and security</h2>
      <p style={style.p}>
        Your data is stored on secure cloud infrastructure with encryption in transit and
        at rest. Each business's data is isolated — one client cannot access another's
        knowledge base. We retain your data only as long as your account is active or as
        required by law.
      </p>
      <p style={style.p}>
        Some of our infrastructure providers (listed in section 5) process data outside
        South Africa. Where this happens, we rely on the safeguards recognised under
        POPIA for cross-border transfers — including that the provider is subject to a
        law, binding agreement, or set of practices that upholds principles substantially
        similar to POPIA's. AI inference for your business's own conversations runs on a
        South Africa-based server we control directly, kept private behind access-controlled
        infrastructure.
      </p>

      <h2 style={style.h2}>5. Sharing & sub-processors</h2>
      <p style={style.p}>
        We do not sell your personal information. We share data only with the service
        providers necessary to operate Vula, under strict confidentiality, and where
        required by South African law:
      </p>
      <ul style={style.ul}>
        <li><strong>Meta Platforms</strong> — WhatsApp Business Platform (message delivery)</li>
        <li><strong>Supabase</strong> — encrypted database & file storage</li>
        <li><strong>Railway</strong> — application hosting</li>
        <li><strong>OpenRouter</strong> — AI model inference for assistant responses</li>
        <li><strong>Yoco / PayFast</strong> — payment processing (when you transact)</li>
      </ul>
      <p style={style.p}>
        Data obtained through the WhatsApp Business Platform is used only to provide the
        Vula service to the connected business and is never used for advertising or sold.
      </p>

      <h2 style={style.h2}>6. Your rights (POPIA)</h2>
      <p style={style.p}>
        You may request access to, correction of, or deletion of your personal
        information at any time by emailing hello@vula.co.za. You may also lodge a
        complaint with the Information Regulator of South Africa.
      </p>

      <h2 style={style.h2}>7. Contact</h2>
      <p style={style.p}>
        Vula Group (Pty) Ltd, Cape Town, South Africa.<br />
        Email: hello@vula.co.za
      </p>
    </div>
  );
}

function Terms() {
  return (
    <div style={style.body}>
      <h1 style={style.h1}>Terms of Service</h1>
      <p style={style.meta}>Last updated: 3 June 2026</p>

      <p style={style.p}>
        These terms govern your use of Vula, an AI business assistant operated by Vula
        Group (Pty) Ltd. By using Vula via WhatsApp or web, you agree to these terms.
      </p>

      <h2 style={style.h2}>1. The service</h2>
      <p style={style.p}>
        Vula provides AI-assisted document generation, knowledge retrieval, order
        management, and business communication tools. Responses are generated by AI and
        should be reviewed before relying on them for legal, financial, or contractual
        decisions.
      </p>

      <h2 style={style.h2}>2. Your responsibilities</h2>
      <ul style={style.ul}>
        <li>You are responsible for the accuracy of information you provide</li>
        <li>You must not use Vula for unlawful purposes</li>
        <li>You must have the right to upload any documents you share with Vula</li>
      </ul>

      <h2 style={style.h2}>3. Payments</h2>
      <p style={style.p}>
        Subscription fees and transaction charges are billed in South African Rand (ZAR).
        Payment terms are presented at signup. Online payments are processed securely via
        our payment partners (Yoco, PayFast).
      </p>

      <h2 style={style.h2}>4. Limitation of liability</h2>
      <p style={style.p}>
        Vula is provided "as is". AI-generated content may contain errors. Vula Group is
        not liable for decisions made based on AI output. Our total liability is limited
        to the fees you paid in the preceding three months.
      </p>

      <h2 style={style.h2}>5. Termination</h2>
      <p style={style.p}>
        You may stop using Vula at any time. We may suspend accounts that breach these
        terms. On termination, you may request export or deletion of your data.
      </p>

      <h2 style={style.h2}>6. Governing law</h2>
      <p style={style.p}>
        These terms are governed by the laws of the Republic of South Africa.
      </p>

      <h2 style={style.h2}>7. Contact</h2>
      <p style={style.p}>
        Vula Group (Pty) Ltd, Cape Town, South Africa. Email: hello@vula.co.za
      </p>
    </div>
  );
}

const style = {
  body: { fontFamily: "system-ui", color: C.text, lineHeight: 1.6 },
  h1: { fontSize: 26, fontWeight: 700, color: C.text, margin: "0 0 4px" },
  h2: { fontSize: 17, fontWeight: 600, color: C.green, margin: "28px 0 8px" },
  meta: { fontSize: 13, color: C.muted, margin: "0 0 24px" },
  p: { fontSize: 14, color: C.text, margin: "0 0 12px" },
  ul: { fontSize: 14, color: C.text, paddingLeft: 20, margin: "0 0 12px" },
};
