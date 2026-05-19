import { Show, UserProfile } from "@clerk/react";
import { Navigate } from "react-router-dom";

import { clerkAppearance } from "@/lib/clerk-appearance";

export default function Account() {
  return (
    <div className="relative min-h-[calc(100vh-9rem)] overflow-hidden px-4 py-8 md:px-6 md:py-12">
      <div className="pointer-events-none absolute inset-0">
        <div className="absolute left-1/2 top-0 h-[32rem] w-[32rem] -translate-x-1/2 rounded-full bg-cyan-300/10 blur-3xl" />
        <div className="absolute bottom-0 left-1/2 h-[24rem] w-[24rem] -translate-x-1/2 rounded-full bg-slate-200/5 blur-3xl" />
      </div>

      <div className="cartosky-clerk-profile relative mx-auto max-w-5xl">
        <Show when="signed-in">
          <UserProfile routing="path" path="/account" appearance={clerkAppearance} />
        </Show>
        <Show when="signed-out">
          <Navigate to="/login" replace />
        </Show>
      </div>
    </div>
  );
}