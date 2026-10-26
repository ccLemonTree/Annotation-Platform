import { createContext, useContext } from 'react';
import type { User } from '../api/auth';

export const CurrentUser = createContext<User | null>(null);
export const useCurrentUser = () => useContext(CurrentUser);
