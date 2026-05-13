import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate } from 'react-router-dom';
import { createList, getLists } from '../api';
import { useState } from 'react';

export default function ListsPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [newListName, setNewListName] = useState('');
  const listsQuery = useQuery({ queryKey: ['lists'], queryFn: getLists });

  const createMutation = useMutation({
    mutationFn: async () => createList(newListName.trim()),
    onSuccess: async (shoppingList) => {
      setNewListName('');
      await queryClient.invalidateQueries({ queryKey: ['lists'] });
      navigate(`/lists/${shoppingList.id}`);
    },
  });

  return (
    <div className="grid gap-5 lg:grid-cols-[minmax(0,1fr)_22rem]">
      <section className="space-y-4">
        <div className="rounded-[34px] border border-white/80 bg-white/95 p-5 shadow-[0_20px_60px_-36px_rgba(15,23,42,0.35)] sm:p-6">
          <p className="text-xs font-semibold uppercase tracking-[0.24em] text-sky-600">Saved baskets</p>
          <h2 className="mt-2 text-2xl font-black text-slate-900">הרשימות שלך</h2>
          <p className="mt-2 text-sm leading-6 text-slate-500">
            לכל רשימה אפשר להגדיר כמויות, להוסיף מוצרים מדויקים, ולקבל השוואה מלאה לפי רשת.
          </p>
        </div>

        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {listsQuery.data?.map((shoppingList) => (
            <Link
              key={shoppingList.id}
              to={`/lists/${shoppingList.id}`}
              aria-label={shoppingList.name}
              className="rounded-[30px] border border-white/80 bg-white/95 p-5 shadow-sm transition hover:-translate-y-0.5 hover:shadow-lg"
            >
              <p className="text-lg font-black text-slate-900">{shoppingList.name}</p>
              <p className="mt-2 text-sm text-slate-500">{shoppingList.item_count} מוצרים</p>
              <p className="mt-1 text-sm text-slate-500">{shoppingList.total_quantity} יחידות בסך הכל</p>
            </Link>
          ))}
        </div>
      </section>

      <aside className="space-y-4">
        <div className="rounded-[34px] border border-slate-200 bg-white/95 p-5 shadow-sm">
          <p className="text-sm font-black text-slate-900">רשימה חדשה</p>
          <div className="mt-4 space-y-3">
            <input
              value={newListName}
              onChange={(event) => setNewListName(event.target.value)}
              placeholder="קניות לשבוע"
              className="min-h-13 w-full rounded-[22px] border border-slate-200 bg-slate-50 px-4 text-base outline-none focus:border-sky-300 focus:bg-white"
            />
            <button
              type="button"
              onClick={() => createMutation.mutate()}
              disabled={newListName.trim().length === 0 || createMutation.isPending}
              className="min-h-13 w-full rounded-[22px] bg-slate-900 px-4 text-base font-black text-white disabled:opacity-60"
            >
              {createMutation.isPending ? 'יוצר...' : 'צור רשימה'}
            </button>
          </div>
        </div>
      </aside>
    </div>
  );
}
