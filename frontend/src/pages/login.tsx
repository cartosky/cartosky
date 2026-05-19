import { Show, SignIn, UserButton } from "@clerk/react";
import { ExternalLink } from "lucide-react";
import { Link } from "react-router-dom";

export default function Login() {
  return (
    <div className="relative min-h-[calc(100vh-9rem)] overflow-hidden px-4 py-10 md:px-6 md:py-16">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-0 h-[32rem] w-[32rem] -translate-x-1/2 rounded-full bg-[#294137]/25 blur-3xl" />
        <div className="absolute bottom-0 left-1/2 h-[24rem] w-[24rem] -translate-x-1/2 rounded-full bg-[#7da08f]/10 blur-3xl" />
      </div>

      <div className="relative mx-auto flex min-h-[calc(100vh-13rem)] max-w-md items-center justify-center">
        <section className="w-full">
          <Show when="signed-out">
            <div className="flex justify-center">
              <SignIn
                routing="hash"
                fallbackRedirectUrl="/viewer"
                signUpFallbackRedirectUrl="/viewer"
                appearance={{
                  elements: {
                    rootBox: "w-full",
                    cardBox: "mx-auto w-full shadow-[0_20px_80px_rgba(0,0,0,0.36)]",
                  },
                }}
              />
            </div>
          </Show>

          <Show when="signed-in">
            <div className="rounded-[24px] border border-white/10 bg-white/[0.045] p-5 shadow-[0_20px_80px_rgba(0,0,0,0.36)] backdrop-blur-2xl">
              <div className="space-y-5">
                <div className="flex items-center justify-between gap-4 rounded-2xl border border-white/10 bg-black/25 p-4">
                  <div className="min-w-0">
                    <div className="text-xs uppercase tracking-[0.22em] text-white/45">Account</div>
                    <div className="mt-1 text-lg font-medium text-white">Signed in</div>
                  </div>
                  <UserButton />
                </div>

                <Link
                  to="/viewer"
                  className="inline-flex items-center gap-1.5 rounded-lg border border-white/15 bg-white/[0.06] px-3 py-2 text-sm font-medium text-white hover:bg-white/[0.1]"
                >
                  Back to viewer
                  <ExternalLink className="h-3.5 w-3.5" />
                </Link>
              </div>
            </div>
          </Show>
        </section>
      </div>
    </div>
  );
}
