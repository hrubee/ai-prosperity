import Link from "next/link";
import { SiteHeader, Footer } from "@/components/nav";

export const metadata = {
  title: "Terms & Conditions & Risk Disclosure | AI Prosperity",
  description: "Terms and conditions, user agreement, and risk disclosure for AI Prosperity Auto Trades application.",
};

export default function TermsPage() {
  return (
    <>
      <SiteHeader />

      <main className="container-x py-12 md:py-20">
        <div className="mx-auto max-w-4xl space-y-12">
          {/* Header */}
          <div className="border-b border-ink-800 pb-8">
            <span className="pill text-gold-400 border-gold-500/40 mb-3">Legal Agreement</span>
            <h1 className="text-3xl font-bold text-white sm:text-5xl">Terms &amp; Conditions &amp; Risk Disclosure</h1>
            <p className="mt-3 text-sm text-muted">
              Application: <strong className="text-white">AI Prosperity Auto Trades</strong>
            </p>
          </div>

          {/* PART 1: TERMS AND CONDITIONS */}
          <section className="card p-6 md:p-10 space-y-6">
            <h2 className="text-2xl font-bold text-white border-b border-ink-800 pb-4">Terms and Conditions</h2>
            
            <div className="space-y-6 text-sm text-muted leading-relaxed">
              <div>
                <h3 className="font-semibold text-white text-base">1. Acceptance of Terms</h3>
                <p className="mt-1">
                  By creating an account, signing into this service, or integrating your Demat account with the Auto Trades application, you acknowledge that you have read, understood, and agree to be bound by these Terms and Conditions in their entirety. If you do not agree with any part of these terms, you must not use this service.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">2. Voluntary Participation and Account Integration</h3>
                <p className="mt-1">
                  The decision to use the Auto Trades application and to link or attach your personal Demat/trading account to our automated trading services is entirely your own. You are acting on your own free will and assume full responsibility for granting our application access to execute trades on your behalf.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">3. Acknowledgment of Financial Risk</h3>
                <p className="mt-1">
                  You explicitly acknowledge that trading in financial markets (including equities, derivatives, commodities, and currencies) involves a substantial risk of loss. Market conditions can change rapidly and unpredictably. By signing into this service, you agree that you fully understand these risks and accept that automated trading algorithms can result in significant financial losses.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">4. Acknowledgment of Unregulated Service</h3>
                <p className="mt-1">
                  You are fully aware that Auto Trades operates as an unregulated service. We are not registered as investment advisors, portfolio managers, or authorized algorithmic trading brokers with any financial regulatory authority. You agree to use this software &quot;as is&quot; and participate in these unregulated services entirely at your own discretion and risk.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">5. Disclaimer of Liability</h3>
                <p className="mt-1">
                  The developers, owners, affiliates, and operators of the Auto Trades app shall not be held liable or responsible for the outcomes of your trading account. We make no guarantees, representations, or warranties regarding potential profits, system uptime, or the accuracy of the automated trades. You alone are responsible for the financial results of the trades executed through your attached Demat account.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">6. Waiver of Claims and Indemnification</h3>
                <p className="mt-1">
                  In the event of partial or total financial loss, system failures, software bugs, or execution delays, you agree that you will not claim, sue, or seek compensation from the Auto Trades app, its creators, or its affiliates in any way, shape, or form. You hereby waive all legal rights to hold us accountable for any direct, indirect, incidental, or consequential damages arising from your use of this service.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">7. Technical Failures and System Availability</h3>
                <p className="mt-1">
                  You understand that automated trading software relies on internet connectivity, third-party broker APIs, and exchange servers. We do not guarantee uninterrupted access or 100% uptime. We are not liable for any financial losses resulting from delayed executions, missed trades, slippage, software bugs, API disconnections, or exchange downtime.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">8. Data Privacy and API Security</h3>
                <p className="mt-1">
                  To facilitate automated trades, you will be required to provide API keys, access tokens, or login credentials for your Demat account. While we implement reasonable security measures to protect this data, we cannot guarantee absolute security against cyberattacks or data breaches. You are solely responsible for monitoring your account activity and safeguarding your primary broker credentials.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">9. No Financial or Investment Advice</h3>
                <p className="mt-1">
                  The Auto Trades application is an execution tool and does not provide personalized investment, tax, or legal advice. The trades executed by the software are based on pre-programmed algorithms and mathematical models. You agree that the use of this software does not constitute a fiduciary relationship, and you should evaluate all trading strategies independently.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">10. User Margin and Account Maintenance</h3>
                <p className="mt-1">
                  You are solely responsible for maintaining sufficient funds, margins, and balances in your linked Demat account to support the automated trades. We are not responsible for any margin penalties, auto-square-offs initiated by your broker, or rejected orders due to insufficient funds or incorrect account settings.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">11. Right to Terminate or Suspend Service</h3>
                <p className="mt-1">
                  We reserve the right to suspend, restrict, or terminate your access to the Auto Trades application at any time, without prior notice, for any reason, including but not limited to suspected misuse, technical issues, or regulatory requirements.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">12. Governing Law and Jurisdiction</h3>
                <p className="mt-1">
                  These Terms and Conditions shall be governed by and construed in accordance with the local laws of India. Any legal disputes arising from the use of this application shall be subject to the exclusive jurisdiction of the courts in Nashik, Maharashtra.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">13. Modifications to Terms</h3>
                <p className="mt-1">
                  We reserve the right to update, modify, or change these Terms and Conditions at any time. Continued use of the Auto Trades application after any such changes shall constitute your consent to the updated terms.
                </p>
              </div>
            </div>
          </section>

          {/* PART 2: RISK DISCLOSURE AND ACKNOWLEDGMENT AGREEMENT */}
          <section className="card p-6 md:p-10 space-y-6 border-loss/30 bg-loss/5">
            <h2 className="text-2xl font-bold text-white border-b border-ink-800 pb-4 flex items-center gap-2">
              <span className="text-loss">⚠️</span> Risk Disclosure &amp; Acknowledgment Agreement
            </h2>
            
            <p className="text-sm text-muted leading-relaxed">
              Before enabling automated trading features, linking your Demat account, or activating any algorithms within the Auto Trades application, you must carefully read, understand, and explicitly acknowledge the following risks. By creating an account or activating automated execution, you confirm that you fully comprehend and accept these risks.
            </p>

            <div className="space-y-6 text-sm text-muted leading-relaxed">
              <div>
                <h3 className="font-semibold text-white text-base">1. High-Risk Nature of Financial Markets</h3>
                <p className="mt-1">
                  Trading in financial markets—including Indian equities, commodities, and crypto—is highly speculative and involves a substantial risk of loss. Market prices can be highly volatile and unpredictable. You acknowledge that you can lose part or all of your initial investment and, in the case of leveraged or margin trading, your losses may exceed your deposited funds.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">2. Risks of Algorithmic and Automated Trading</h3>
                <p className="mt-1">
                  The Auto Trades software relies on pre-programmed algorithms to execute buy and sell orders. You understand that:
                </p>
                <ul className="list-disc list-inside mt-2 space-y-1 pl-2">
                  <li><strong>Past Performance is Not Indicative of Future Results:</strong> Backtested data, historical performance, or simulated track records do not guarantee future profitability. Market conditions change, and algorithms may fail to adapt.</li>
                  <li><strong>Execution Risks:</strong> Automated trades are subject to &quot;slippage&quot; (the difference between expected price and actual execution price), particularly in volatile or illiquid markets.</li>
                  <li><strong>System Failures:</strong> Relying on external infrastructure (broker APIs, internet connectivity, exchange servers) means outages, latency, rate limits, or software bugs may result in missed trades or duplicated orders. We are not liable for losses caused by technical failures.</li>
                </ul>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">3. Unregulated Status of the Service</h3>
                <p className="mt-1">
                  You explicitly acknowledge that the Auto Trades application is an unregulated software tool. We and our operators act solely as software providers, not as SEBI-registered Investment Advisors (RIA) or Portfolio Managers (PMS). The algorithms provided do not take into account your personal financial situation, risk tolerance, or investment goals.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">4. No Guarantee of Profit</h3>
                <p className="mt-1">
                  There is absolutely no guarantee, representation, or warranty that the use of the Auto Trades application will result in profits. You are using the software entirely at your own financial risk.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">5. User Monitoring and Intervention</h3>
                <p className="mt-1">
                  While the application is designed to operate automatically, you acknowledge that it is your sole responsibility to continuously monitor your primary broker and Demat account. You must ensure sufficient margin is maintained and be prepared to manually intervene, halt algorithms, or square off positions directly through your broker if the software behaves unexpectedly.
                </p>
              </div>

              <div>
                <h3 className="font-semibold text-white text-base">6. Limitation of Liability</h3>
                <p className="mt-1">
                  Under no circumstances shall the company, its founders, developers, or affiliates be held liable for any direct, indirect, incidental, or consequential financial losses incurred through the use of the Auto Trades application.
                </p>
              </div>
            </div>
          </section>

          <div className="text-center pt-4">
            <Link href="/signup" className="btn-gold px-8 py-3 text-base">
              Back to Signup →
            </Link>
          </div>
        </div>
      </main>

      <Footer />
    </>
  );
}
