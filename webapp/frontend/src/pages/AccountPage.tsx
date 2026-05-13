import { useQuery } from '@tanstack/react-query';
import { getCatalogStatus, getChains } from '../api';
import { useAuth } from '../app/AuthProvider';
import { formatRelativeDate } from '../lib/format';

export default function AccountPage() {
  const { user } = useAuth();
  const statusQuery = useQuery({ queryKey: ['catalog-status'], queryFn: getCatalogStatus });
  const chainsQuery = useQuery({ queryKey: ['chains'], queryFn: getChains });

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <section className="space-y-4">
        <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-[0_20px_60px_-36px_rgba(15,23,42,0.35)] sm:p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-600">Account</p>
          <h2 className="mt-2 text-2xl font-black text-slate-900">{user?.username}</h2>
          <p className="mt-2 text-sm text-slate-500">נוצר בתאריך {formatRelativeDate(user?.created_at)}</p>
        </div>

        <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-sm sm:p-6">
          <p className="text-sm font-black text-slate-900">מצב קטלוג</p>
          <div className="mt-4 grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <MetricCard label="מתזמן" value={statusQuery.data?.scheduler_running ? 'פעיל' : 'כבוי'} />
            <MetricCard label="רענון אחרון" value={formatRelativeDate(statusQuery.data?.last_successful_refresh?.finished_at)} />
            <MetricCard label="מוצרים שנקלטו" value={String(statusQuery.data?.last_successful_refresh?.products_upserted ?? 0)} />
          </div>
        </div>
      </section>

      <aside className="space-y-4">
        <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-sm">
          <p className="text-sm font-black text-slate-900">רשתות פעילות</p>
          <div className="mt-4 space-y-2">
            {chainsQuery.data?.map((chain) => (
              <div key={chain.chain} className="rounded-2xl bg-slate-50 px-4 py-3 text-sm text-slate-700">
                <div className="flex items-center justify-between gap-3">
                  <span className="font-bold">{chain.label}</span>
                  <span className={chain.enabled ? 'text-emerald-700' : 'text-amber-700'}>
                    {chain.enabled ? 'פעיל' : 'לא זמין'}
                  </span>
                </div>
                {!chain.enabled && chain.unavailable_reason && (
                  <p className="mt-2 text-xs leading-5 text-slate-500">{chain.unavailable_reason}</p>
                )}
              </div>
            ))}
          </div>
        </div>
      </aside>
    </div>
  );
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="rounded-[28px] border border-slate-200 bg-slate-50 px-4 py-4 shadow-sm">
      <p className="text-xs font-semibold uppercase tracking-[0.2em] text-slate-400">{label}</p>
      <p className="mt-3 text-lg font-black text-slate-900">{value}</p>
    </div>
  );
}
