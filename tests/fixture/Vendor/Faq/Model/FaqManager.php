<?php
declare(strict_types=1);

namespace Vendor\Faq\Model;

use Magento\Framework\Event\ManagerInterface;
use Vendor\Faq\Api\FaqRepositoryInterface;

class FaqManager
{
    public function __construct(
        private readonly ManagerInterface $eventManager,
        private readonly FaqRepositoryInterface $faqRepository
    ) {
    }

    public function save(int $id): void
    {
        $this->validate($id);
        $faq = $this->faqRepository->getById($id);
        $this->eventManager->dispatch('faq_save_after', ['faq' => $faq]);
    }

    private function validate(int $id): void
    {
        if ($id <= 0) {
            throw new \InvalidArgumentException('FAQ id must be positive.');
        }
    }
}
