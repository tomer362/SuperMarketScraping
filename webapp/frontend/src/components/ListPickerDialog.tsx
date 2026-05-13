import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { addListItem, createList, getLists } from '../api';
import { formatQuantity } from '../lib/format';
import type { ProductDetail, ShoppingListSummary } from '../types';

interface ListPickerDialogProps {
  product: ProductDetail;
  isOpen: boolean;
  onClose: () => void;
}

export default function ListPickerDialog({ product, isOpen, onClose }: ListPickerDialogProps) {
  const [newListName, setNewListName] = useState('');
  const queryClient = useQueryClient();
  const listsQuery = useQuery({ queryKey: ['lists'], queryFn: getLists, enabled: isOpen });

  const addMutation = useMutation({
    mutationFn: async (shoppingList: ShoppingListSummary) => addListItem(shoppingList.id, product.id, 1),
    onSuccess: async (_, shoppingList) => {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['lists'] }),
        queryClient.invalidateQueries({ queryKey: ['list', shoppingList.id] }),
      ]);
      onClose();
    },
  });

  const createMutation = useMutation({
    mutationFn: async () => {
      const created = await createList(newListName.trim());
      return addListItem(created.id, product.id, 1);
    },
    onSuccess: async () => {
      setNewListName('');
      await queryClient.invalidateQueries({ queryKey: ['lists'] });
      onClose();
    },
  });

  if (!isOpen) {
    return null;
  }

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-slate-950/45 px-3 pb-3 sm:items-center">
      <div className="w-full max-w-md rounded-[32px] bg-white p-5 shadow-2xl shadow-slate-950/30">
        <div className="flex items-start justify-between gap-4">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.2em] text-sky-600">רשימות</p>
            <h2 className="mt-1 text-xl font-black text-slate-900">הוסף/י את המוצר</h2>
            <p className="mt-1 text-sm text-slate-500">{product.name}</p>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-full border border-slate-200 px-3 py-1 text-sm font-semibold text-slate-600"
          >
            סגור
          </button>
        </div>

        <div className="mt-5 space-y-3">
          {listsQuery.data?.map((shoppingList) => (
            <button
              key={shoppingList.id}
              type="button"
              onClick={() => addMutation.mutate(shoppingList)}
              aria-label={`הוסף לרשימה ${shoppingList.name}`}
              className="flex min-h-16 w-full items-center justify-between rounded-[24px] border border-slate-200 px-4 py-3 text-right transition hover:bg-slate-50"
            >
              <div>
                <p className="text-sm font-bold text-slate-900">{shoppingList.name}</p>
                <p className="text-xs text-slate-500">
                  {shoppingList.item_count} מוצרים · סה״כ כמות {formatQuantity(shoppingList.total_quantity)}
                </p>
              </div>
              <span className="rounded-full bg-sky-50 px-3 py-1 text-xs font-bold text-sky-700">
                הוסף
              </span>
            </button>
          ))}
        </div>

        <div className="mt-6 rounded-[24px] border border-dashed border-slate-300 bg-slate-50 p-4">
          <label htmlFor="new-list-name" className="mb-2 block text-sm font-semibold text-slate-700">
            יצירת רשימה חדשה
          </label>
          <div className="flex gap-2">
            <input
              id="new-list-name"
              value={newListName}
              onChange={(event) => setNewListName(event.target.value)}
              placeholder="למשל: קניות לשבוע"
              className="min-h-12 flex-1 rounded-full border border-slate-200 bg-white px-4 text-sm outline-none focus:border-sky-300"
            />
            <button
              type="button"
              onClick={() => createMutation.mutate()}
              disabled={newListName.trim().length === 0 || createMutation.isPending}
              className="rounded-full bg-slate-900 px-4 text-sm font-bold text-white disabled:opacity-50"
            >
              צור
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
