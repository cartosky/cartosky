type PolicySection = {
  title: string;
  children: Array<PolicyBlock>;
};

type PolicyBlock =
  | { type: "heading"; text: string }
  | { type: "paragraph"; text: string }
  | { type: "list"; items: string[] };

const policySections: PolicySection[] = [
  {
    title: "1. Introduction",
    children: [
      {
        type: "paragraph",
        text: 'CartoSky ("we," "us," or "our") operates the website located at cartosky.com (the "Service"), a map-first weather analysis platform providing forecast visualization, model guidance, and location-based weather data.',
      },
      {
        type: "paragraph",
        text: "This Privacy Policy explains what information we collect, how we use it, who we share it with, and the choices available to you. By using the Service, you agree to the practices described in this policy.",
      },
    ],
  },
  {
    title: "2. Information We Collect",
    children: [
      { type: "heading", text: "2.1 Account and Authentication Information" },
      {
        type: "paragraph",
        text: "When you create a CartoSky account or sign in through a third-party provider, we collect:",
      },
      {
        type: "list",
        items: [
          "Your name and email address",
          "A unique identifier from your authentication provider (Google, Facebook, or X/Twitter)",
          "Profile image URL, if provided by your authentication provider",
          "Account creation date and last sign-in time",
        ],
      },
      {
        type: "paragraph",
        text: "Authentication is handled through Clerk (clerk.com). We do not store your passwords. When you use a social login (Google, Facebook, X), we receive only the information those providers share with us based on your privacy settings with them.",
      },
      { type: "heading", text: "2.2 Usage and Interaction Data" },
      {
        type: "paragraph",
        text: "As you use the Service, we automatically collect information about your interactions, including:",
      },
      {
        type: "list",
        items: [
          "Pages and features visited within CartoSky",
          "Weather models, variables, and forecast hours viewed",
          "Geographic locations searched or pinned",
          "Timestamps and session duration",
          "Referring URLs and general navigation patterns",
        ],
      },
      {
        type: "paragraph",
        text: "This data is collected through our backend telemetry systems and, where enabled, through PostHog for product analytics. This information is used in aggregate to improve the Service and is not used to identify you individually in most cases.",
      },
      { type: "heading", text: "2.3 Technical and Device Information" },
      {
        type: "paragraph",
        text: "We collect standard technical information when you access the Service, including:",
      },
      {
        type: "list",
        items: [
          "IP address (used for approximate geolocation and security purposes)",
          "Browser type and version",
          "Operating system",
          "Device type",
          "Network performance metrics relevant to map tile and forecast data delivery",
        ],
      },
      {
        type: "paragraph",
        text: "Our infrastructure uses Prometheus for operational metrics and OpenTelemetry for performance tracing. This data is used solely for operating and improving the Service.",
      },
      { type: "heading", text: "2.4 Location Information" },
      {
        type: "paragraph",
        text: "If you use the Forecast page or location-based features, you may provide a location by searching for a city or address. We use this information to return weather data relevant to that location. We do not persistently store precise location data beyond what is necessary to fulfill your request unless you save a location to your account.",
      },
      { type: "heading", text: "2.5 Communications" },
      {
        type: "paragraph",
        text: "If you contact us at privacy@cartosky.com or through any feedback mechanism on the Service, we collect the content of your communication and any contact information you provide.",
      },
      { type: "heading", text: "2.6 Information We Do Not Collect" },
      { type: "paragraph", text: "We do not collect or store:" },
      {
        type: "list",
        items: [
          "Payment card numbers or banking information (payment processing, when introduced, will be handled entirely by Stripe and subject to their privacy policy)",
          "Government-issued identification numbers",
          "Sensitive personal information such as health data, racial or ethnic origin, or political opinions",
        ],
      },
    ],
  },
  {
    title: "3. How We Use Your Information",
    children: [
      { type: "paragraph", text: "We use the information we collect for the following purposes:" },
      {
        type: "list",
        items: [
          "To create and manage your CartoSky account",
          "To authenticate your identity and maintain session security",
          "To provide weather forecast data, model visualization, and location-based features",
          "To operate and improve the reliability and performance of the Service",
          "To understand how the Service is used and prioritize product development",
          "To detect, investigate, and prevent fraudulent, unauthorized, or illegal activity",
          "To respond to your support requests and communications",
          "To send you transactional communications related to your account (e.g., security notifications)",
          "To comply with applicable laws and legal obligations",
        ],
      },
      {
        type: "paragraph",
        text: "We do not use your information to serve third-party advertising. We do not sell your personal information to any third party.",
      },
    ],
  },
  {
    title: "4. How We Share Your Information",
    children: [
      { type: "heading", text: "4.1 Service Providers" },
      {
        type: "paragraph",
        text: "We share information with trusted third-party service providers who help us operate the Service. These providers are contractually obligated to use your information only as directed by us and in accordance with this policy. Current providers include:",
      },
      {
        type: "list",
        items: [
          "Clerk (clerk.com) - authentication and user identity management",
          "PostHog - product analytics (where enabled)",
          "Stripe - payment processing (when subscription features are introduced)",
          "The Weather Forums - content sharing integration, only when you explicitly initiate a share action",
        ],
      },
      { type: "heading", text: "4.2 Legal Requirements" },
      {
        type: "paragraph",
        text: "We may disclose your information if we believe in good faith that such disclosure is necessary to comply with applicable law, respond to a valid legal process, protect the rights or safety of CartoSky, our users, or the public, or enforce our Terms of Service.",
      },
      { type: "heading", text: "4.3 Business Transfers" },
      {
        type: "paragraph",
        text: "If CartoSky is involved in a merger, acquisition, or sale of assets, your information may be transferred as part of that transaction. We will notify you via email or a prominent notice on the Service before your information becomes subject to a materially different privacy policy.",
      },
      { type: "heading", text: "4.4 Aggregate and De-identified Data" },
      {
        type: "paragraph",
        text: "We may share aggregate, de-identified, or anonymized information that cannot reasonably be used to identify you, for purposes such as industry research, improving weather data services, or public reporting.",
      },
    ],
  },
  {
    title: "5. Data Retention",
    children: [
      {
        type: "paragraph",
        text: "We retain your account information for as long as your account is active or as needed to provide you with the Service. Operational telemetry and usage logs are retained for a limited period consistent with their operational purpose, generally between 30 and 90 days.",
      },
      {
        type: "paragraph",
        text: "If you delete your account, we will delete or anonymize your personal information within a reasonable period, except where we are required to retain it for legal or compliance purposes.",
      },
    ],
  },
  {
    title: "6. Your Rights and Choices",
    children: [
      { type: "heading", text: "6.1 Access and Correction" },
      {
        type: "paragraph",
        text: "You may access and update your account information at any time through your account settings or by contacting us at privacy@cartosky.com.",
      },
      { type: "heading", text: "6.2 Account Deletion" },
      {
        type: "paragraph",
        text: "You may request deletion of your CartoSky account by contacting us at privacy@cartosky.com. Upon deletion, we will remove your personal information from our active systems subject to any legal retention requirements.",
      },
      { type: "heading", text: "6.3 Linked Social Accounts" },
      {
        type: "paragraph",
        text: "If you signed in using a social provider (Google, Facebook, X), you can manage the permissions CartoSky has with that provider through that provider's account settings. Revoking access at the provider level does not automatically delete your CartoSky account.",
      },
      { type: "heading", text: "6.4 Analytics Opt-Out" },
      {
        type: "paragraph",
        text: "Where we use PostHog for product analytics, you may opt out of behavioral tracking by contacting us at privacy@cartosky.com.",
      },
      { type: "heading", text: "6.5 California Residents (CCPA)" },
      {
        type: "paragraph",
        text: "If you are a California resident, you have the right to know what personal information we collect, request deletion of your personal information, and opt out of the sale of personal information. We do not sell personal information. To exercise your rights, contact privacy@cartosky.com.",
      },
      { type: "heading", text: "6.6 EEA, UK, and Swiss Residents (GDPR)" },
      {
        type: "paragraph",
        text: "If you are located in the European Economic Area, United Kingdom, or Switzerland, you have additional rights under applicable data protection law, including the right to access, rectify, erase, restrict, or object to our processing of your personal information, and the right to data portability. You also have the right to lodge a complaint with your local supervisory authority.",
      },
      {
        type: "paragraph",
        text: "Our legal basis for processing your personal information is typically the performance of our contract with you (providing the Service), our legitimate interests in operating and improving the Service, or your consent where specifically required.",
      },
      {
        type: "paragraph",
        text: "To exercise any of these rights, please contact us at privacy@cartosky.com.",
      },
    ],
  },
  {
    title: "7. Cookies and Local Storage",
    children: [
      {
        type: "paragraph",
        text: "CartoSky uses cookies and browser local storage to maintain your session, remember your preferences (such as selected weather model or map settings), and support authentication via Clerk.",
      },
      {
        type: "paragraph",
        text: "We do not use advertising cookies or third-party tracking cookies for ad targeting purposes. You may configure your browser to refuse cookies, though doing so may affect the functionality of the Service.",
      },
    ],
  },
  {
    title: "8. Security",
    children: [
      {
        type: "paragraph",
        text: "We implement reasonable technical and organizational measures to protect your information against unauthorized access, loss, or misuse. Authentication is handled through Clerk, which maintains its own security certifications and practices. All data in transit is encrypted using TLS.",
      },
      {
        type: "paragraph",
        text: "No method of transmission over the internet or electronic storage is completely secure. While we work to protect your information, we cannot guarantee absolute security.",
      },
    ],
  },
  {
    title: "9. Third-Party Services and Links",
    children: [
      {
        type: "paragraph",
        text: "The Service integrates with third-party services including The Weather Forums, weather data providers (including NOAA/NWS, ECMWF, and Open-Meteo), and mapping services. This Privacy Policy applies only to CartoSky. Your use of third-party services is governed by their respective privacy policies. We encourage you to review those policies before sharing information with any third party.",
      },
    ],
  },
  {
    title: "10. Children's Privacy",
    children: [
      {
        type: "paragraph",
        text: "CartoSky is not directed at children under the age of 13. We do not knowingly collect personal information from children under 13. If you believe we have inadvertently collected information from a child under 13, please contact us at privacy@cartosky.com and we will promptly delete it.",
      },
    ],
  },
  {
    title: "11. Changes to This Privacy Policy",
    children: [
      {
        type: "paragraph",
        text: "We may update this Privacy Policy from time to time. When we make material changes, we will update the effective date at the top of this page and, where appropriate, notify you by email or through a notice on the Service. Your continued use of the Service after any changes constitutes your acceptance of the updated policy.",
      },
      {
        type: "paragraph",
        text: "We encourage you to review this policy periodically to stay informed about how we protect your information.",
      },
    ],
  },
  {
    title: "12. Contact Us",
    children: [
      {
        type: "paragraph",
        text: "If you have any questions, concerns, or requests regarding this Privacy Policy or our data practices, please contact us at:",
      },
      { type: "paragraph", text: "CartoSky" },
      { type: "paragraph", text: "Email: privacy@cartosky.com" },
      { type: "paragraph", text: "Website: cartosky.com" },
    ],
  },
];

function SectionEyebrow({ children }: { children: React.ReactNode }) {
  return (
    <div className="inline-flex items-center gap-2 text-[10px] font-semibold uppercase tracking-[0.28em] text-cyan-200/70">
      <span className="h-px w-7 bg-cyan-300/45" />
      <span>{children}</span>
    </div>
  );
}

function splitSectionTitle(title: string) {
  const match = title.match(/^(\d+)\.\s+(.+)$/);

  if (!match) {
    return { number: "", label: title };
  }

  return { number: match[1].padStart(2, "0"), label: match[2] };
}

function PolicyBlockView({ block }: { block: PolicyBlock }) {
  if (block.type === "heading") {
    return <h3 className="mt-7 text-base font-semibold tracking-tight text-cyan-100 md:text-lg">{block.text}</h3>;
  }

  if (block.type === "list") {
    return (
      <ul className="mt-4 space-y-2.5 border-l border-cyan-200/18 pl-5 text-sm leading-7 text-white/68 md:text-base md:leading-8">
        {block.items.map((item) => (
          <li key={item} className="relative before:absolute before:-left-[1.36rem] before:top-[0.78rem] before:h-1.5 before:w-1.5 before:rounded-full before:bg-cyan-200/70">
            {item}
          </li>
        ))}
      </ul>
    );
  }

  return <p className="mt-4 text-sm leading-7 text-white/68 md:text-base md:leading-8">{block.text}</p>;
}

export default function Privacy() {
  return (
    <div className="relative left-1/2 right-1/2 -mt-12 w-screen -translate-x-1/2 text-white md:-mt-16">
      <section className="border-b border-white/8 bg-[#07111f] px-5 pb-12 pt-24 md:px-8 md:pb-14 md:pt-28">
        <div className="mx-auto grid max-w-6xl gap-10 lg:grid-cols-[1.15fr_0.85fr] lg:items-end">
          <div className="max-w-3xl">
            <SectionEyebrow>Privacy</SectionEyebrow>
            <h1 className="mt-6 text-balance text-4xl font-semibold tracking-[-0.04em] text-white md:text-6xl md:leading-[0.98]">
              Privacy Policy,
              <br />
              <span className="font-['Georgia','Times_New_Roman',serif] font-normal italic text-cyan-200">
                plainly stated.
              </span>
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-white/72 md:text-[1.02rem]">
              This policy explains what CartoSky collects, how it is used, who it may be shared with, and the choices available to you.
            </p>
          </div>

          <div className="border-l border-white/8 pl-5 lg:pl-7">
            <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-white/42">Policy Details</div>
            <div className="mt-5 space-y-4">
              <div>
                <div className="text-2xl font-semibold tracking-tight text-white">May 19, 2026</div>
                <div className="mt-1 text-sm text-white/58">Effective date.</div>
              </div>
              <div className="h-px bg-white/8" />
              <div>
                <div className="text-2xl font-semibold tracking-tight text-white">12</div>
                <div className="mt-1 text-sm text-white/58">Policy sections.</div>
              </div>
            </div>
          </div>
        </div>
      </section>

      <section className="bg-[#0b1527] px-5 py-12 md:px-8 md:py-16">
        <div className="mx-auto max-w-6xl">
          <div className="mb-8 flex flex-col gap-3 border-b border-white/8 pb-6 md:flex-row md:items-end md:justify-between">
            <div>
              <SectionEyebrow>Full Text</SectionEyebrow>
              <h2 className="mt-4 text-3xl font-semibold tracking-tight text-white md:text-4xl">CartoSky Privacy Policy</h2>
            </div>
            <div className="text-sm text-white/50">cartosky.com</div>
          </div>

        {policySections.map((section) => (
          <section key={section.title} className="grid gap-5 border-t border-white/8 py-8 first:border-t-0 md:grid-cols-[0.28fr_0.72fr] md:gap-10 md:py-10">
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-[0.24em] text-cyan-200/62">
                Section {splitSectionTitle(section.title).number}
              </div>
              <h3 className="mt-3 text-2xl font-semibold tracking-tight text-white md:text-3xl">
                {splitSectionTitle(section.title).label}
              </h3>
            </div>
            <div className="max-w-3xl">
              {section.children.map((block, index) => (
                <PolicyBlockView key={`${section.title}-${index}`} block={block} />
              ))}
            </div>
          </section>
        ))}

          <div className="border-t border-white/10 pt-8 text-sm text-white/48">&copy; 2026 CartoSky. All rights reserved.</div>
        </div>
      </section>
    </div>
  );
}