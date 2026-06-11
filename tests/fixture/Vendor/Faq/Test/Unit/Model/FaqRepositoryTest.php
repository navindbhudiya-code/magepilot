<?php
declare(strict_types=1);

namespace Vendor\Faq\Test\Unit\Model;

use PHPUnit\Framework\TestCase;
use Vendor\Faq\Model\FaqRepository;
use Vendor\Faq\Model\ResourceModel\Faq\CollectionFactory;

class FaqRepositoryTest extends TestCase
{
    public function testInstance(): void
    {
        $factory = $this->createMock(CollectionFactory::class);
        $this->assertInstanceOf(FaqRepository::class, new FaqRepository($factory));
    }
}
