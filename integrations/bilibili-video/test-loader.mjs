// Resolve the installed portable runtime's public aliases for isolated plugin tests.
import { registerHooks } from 'node:module';

registerHooks({
  resolve(specifier, context, next) {
    if (specifier.startsWith('cortico/')) {
      return next(new URL('../../.research/Coopanion/resources/app/build/cortico/src/' + specifier.slice(8), import.meta.url).href, context);
    }
    return next(specifier, context);
  },
});
