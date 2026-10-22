declare module "@decky/api" {
  export function call<Args extends any[] = [], Return = void>(route: string, ...args: Args): Promise<Return>;
  export const routerHook: { addRoute(path: string, component: any, props?: any): void; removeRoute(path: string): void; [key: string]: any };
  export const toaster: { toast(toast: { title?: any; body?: any; duration?: number; [key: string]: any }): void };
  export function definePlugin(fn: () => any): any;
}
