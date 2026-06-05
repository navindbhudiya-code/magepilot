# Magento 2 Exceptions & Error Handling (correct classes + HTTP/GraphQL mapping)

## Use the framework exception classes — do NOT invent class names
There is **NO `InvalidEmailException`** in Magento. For invalid input (including a bad email address),
throw **`Magento\Framework\Exception\InputException`** (or the base `LocalizedException`). The standard
framework exceptions (all under `Magento\Framework\Exception\…`) and how the REST web API maps them:

| Exception | REST HTTP status | Use for |
|---|---|---|
| `NoSuchEntityException` | 404 | entity not found (e.g. `getById` miss) |
| `InputException` | 400 | invalid/malformed input (bad email, missing/out-of-range field) |
| `CouldNotSaveException` | 400 | persistence failure on save |
| `CouldNotDeleteException` | 400 | persistence failure on delete |
| `AlreadyExistsException` | 400 | unique-key conflict |
| `AuthorizationException` | 403 | not permitted |
| `AuthenticationException` | 401 | not authenticated |
| `StateException` | 409 | invalid state / conflict |
| `LocalizedException` (base) | 400 | generic user-facing error |
| uncaught `\Throwable` | 500 | unexpected (log it; hide details in production) |

`InputException` has helpers: `InputException::invalidFieldValue('email', $value)` and
`InputException::requiredField('name')`.

## Invalid email → InputException (not a made-up class)
```php
if (!\filter_var($email, FILTER_VALIDATE_EMAIL)) {
    throw new \Magento\Framework\Exception\InputException(__('Please enter a valid email address.'));
}
```

## GraphQL exception classes (exact names)
Under `Magento\Framework\GraphQl\Exception\…`:
- `GraphQlInputException` — invalid input.
- `GraphQlNoSuchEntityException` — not found. (Note the word order: GraphQl-NoSuchEntity-Exception, **not**
  `NoSuchEntityGraphQlException`.)
- `GraphQlAuthorizationException` — not authorized. **Use this for "customer is not logged in"**, not
  `GraphQlInputException`.
- `GraphQlAuthenticationException` — not authenticated.

## Controller try/catch — one catch per type (no duplicate catches)
You CANNOT catch the same exception type twice — PHP fatals with "Catch type … is already caught". Catch
the most specific first, then the base `LocalizedException`, then `\Throwable`:
```php
try {
    $thing = $id ? $this->repository->getById($id) : $this->factory->create();
    $thing->setTitle($title);
    $this->repository->save($thing);
    $this->messageManager->addSuccessMessage(__('Saved.'));
    return $redirect->setPath('*/*/edit', ['id' => $thing->getId()]);
} catch (\Magento\Framework\Exception\NoSuchEntityException $e) {
    $this->messageManager->addErrorMessage(__('This record no longer exists.'));
} catch (\Magento\Framework\Exception\LocalizedException $e) {   // covers InputException, CouldNotSave, AlreadyExists
    $this->messageManager->addErrorMessage($e->getMessage());
} catch (\Throwable $e) {
    $this->logger->error($e->getMessage(), ['exception' => $e]);
    $this->messageManager->addErrorMessage(__('Something went wrong.'));
}
return $redirect->setPath('*/*/');
```
`InputException`, `CouldNotSaveException`, and `AlreadyExistsException` all extend `LocalizedException`, so a
single `catch (LocalizedException $e)` handles them — never write a separate (duplicate) catch for each.
Declare the entity before the `try` (or redirect to the listing) so it isn't undefined on the error path.
