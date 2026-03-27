import { useQuery, useMutation } from '@tanstack/react-query';
import { getScrapeStatus, triggerScrape } from '../api';

export default function StatusBar() {
  const { data } = useQuery({
    queryKey: ['scrape-status'],
    queryFn: getScrapeStatus,
    refetchInterval: 30_000,
    staleTime: 20_000,
  });

  const mutation = useMutation({ mutationFn: triggerScrape });

  const lastRun = data?.last_run as Record<string, unknown> | undefined;

  return (
    <div className="bg-white border-b border-slate-100 px-4 py-2 flex items-center justify-between text-xs text-slate-400 gap-2 flex-wrap">
      <div className="flex items-center gap-3">
        <span className="flex items-center gap-1.5">
          <span
            className={`w-2 h-2 rounded-full inline-block ${
              data?.scheduler_running ? 'bg-emerald-400' : 'bg-slate-300'
            }`}
          />
          {data?.scheduler_running ? 'מתזמן פועל' : 'מתזמן כבוי'}
        </span>
        {data?.interval_hours && (
          <span>כל {data.interval_hours}ש׳</span>
        )}
        {lastRun?.finished_at != null && (
          <span>
            עדכון אחרון:{' '}
            {new Date(lastRun.finished_at as string).toLocaleString('he-IL', {
              hour: '2-digit',
              minute: '2-digit',
              day: '2-digit',
              month: '2-digit',
            })}
          </span>
        )}
        {lastRun?.products_upserted != null && (
          <span>
            {(lastRun.products_upserted as number).toLocaleString('he-IL')} מוצרים
          </span>
        )}
      </div>

      <button
        onClick={() => mutation.mutate()}
        disabled={mutation.isPending}
        className="text-blue-500 hover:text-blue-700 disabled:opacity-50 transition font-medium"
      >
        {mutation.isPending ? 'מבצע סריקה...' : 'סרוק עכשיו'}
      </button>
    </div>
  );
}
