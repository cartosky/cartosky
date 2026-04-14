import { Link } from "react-router-dom";

export default function SiteFooter() {
  return (
    <footer className="border-t border-white/8 bg-[#07101d]">
      <div className="mx-auto grid max-w-6xl gap-10 px-5 py-12 md:grid-cols-[1.15fr_0.85fr_0.85fr] md:px-8">
        <div>
          <div className="text-lg font-semibold tracking-tight text-white">CartoSky</div>
          <p className="mt-3 max-w-sm text-sm leading-7 text-white/56">
            Serious weather guidance in a cleaner map-first interface for model switching, forecast
            timing, and seasonal analysis.
          </p>
        </div>

        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/38">Explore</div>
          <div className="mt-4 flex flex-col gap-3 text-sm text-white/68">
            <Link to="/viewer" className="transition duration-150 hover:text-white">
              Viewer
            </Link>
            <Link to="/forecast" className="transition duration-150 hover:text-white">
              Forecast
            </Link>
            <Link to="/models" className="transition duration-150 hover:text-white">
              Models
            </Link>
            <Link to="/variables" className="transition duration-150 hover:text-white">
              Variables
            </Link>
            <Link to="/login" className="transition duration-150 hover:text-white">
              Login
            </Link>
          </div>
        </div>

        <div>
          <div className="text-[10px] font-semibold uppercase tracking-[0.22em] text-white/38">Resources</div>
          <div className="mt-4 flex flex-col gap-3 text-sm text-white/68">
            <Link to="/login" className="transition duration-150 hover:text-white">
              Account
            </Link>
            <Link to="/models" className="transition duration-150 hover:text-white">
              Model Catalog
            </Link>
            <Link to="/variables" className="transition duration-150 hover:text-white">
              Variable Guide
            </Link>
          </div>
        </div>
      </div>

      <div className="border-t border-white/6">
        <div className="mx-auto flex max-w-6xl flex-col gap-2 px-5 py-5 text-[11px] uppercase tracking-[0.18em] text-white/32 md:flex-row md:items-center md:justify-between md:px-8">
          <span>© {new Date().getFullYear()} CartoSky</span>
          <span>Model data, clearly rendered.</span>
        </div>
      </div>
    </footer>
  );
}
